/*
 * launcher.c - a small simavr frontend for the sim-avr kit.
 *
 * It loads an ELF into a simulated AVR, optionally exposes a gdb server,
 * runs the program at (about) real-time speed, and prints what the I/O ports
 * are doing on stdout so other tools (the browser viewer) can follow along.
 *
 * stdout protocol (one event per line):
 *   io <port> <ddr> <port> <pin>   e.g. "io B 0f 05 05"
 *   state <running|stopped>
 *   time <cycles> <instructions> <freq>   totals since reset, e.g. "time 197523 65880 16000000"
 *   exit <reason>
 * Human-readable messages go to stderr.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "sim_avr.h"
#include "sim_elf.h"
#include "sim_gdb.h"
#include "avr_ioport.h"

/* Protocol output stream. simavr itself printf()s to stdout (e.g. "avr_gdb_init
 * listening..."), so main() moves fd 1 to stderr and keeps a private copy here. */
static FILE *out;

/*
 * SIGINT/SIGTERM handler. While the CPU is halted with no gdb connected,
 * simavr sits in a blocking accept() and retries it after a signal, so a
 * flag checked by the main loop would never be seen. We therefore report
 * the exit and leave right here, using only async-signal-safe calls
 * (the OS closes the sockets for us).
 */
static int out_fd = 1;
static void on_signal(int sig)
{
	static const char msg[] = "exit signal\n";
	(void)sig;
	if (write(out_fd, msg, sizeof msg - 1) < 0) { /* nothing useful to do */ }
	_exit(0);
}

/* Monotonic wall-clock time in nanoseconds. */
static int64_t now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void sleep_ns(int64_t ns)
{
	struct timespec ts = { (time_t)(ns / 1000000000LL), (long)(ns % 1000000000LL) };
	nanosleep(&ts, NULL);	/* an interrupted sleep is fine: we loop again */
}

/* Last values printed for ports B, C, D (-1 = nothing printed yet). */
static const char PORTS[3] = { 'B', 'C', 'D' };
static int last[3][3] = { { -1, -1, -1 }, { -1, -1, -1 }, { -1, -1, -1 } };

/* Print an "io" line for every port whose DDR/PORT/PIN changed. */
static void emit_io(avr_t *avr)
{
	for (int i = 0; i < 3; i++) {
		avr_ioport_state_t st;
		if (avr_ioctl(avr, AVR_IOCTL_IOPORT_GETSTATE(PORTS[i]), &st) != 0)
			continue;	/* this MCU has no such port */
		int v[3] = { st.ddr, st.port, st.pin };
		if (memcmp(v, last[i], sizeof v) == 0)
			continue;
		memcpy(last[i], v, sizeof v);
		fprintf(out, "io %c %02x %02x %02x\n", PORTS[i], v[0], v[1], v[2]);
	}
	fflush(out);
}

static void emit_state(int stopped)
{
	fprintf(out, "state %s\n", stopped ? "stopped" : "running");
	fflush(out);
}

/*
 * Stopwatch counters. Time comes from the simulated cycle count, never the
 * wall clock: at 16 MHz one cycle is exactly 62.5 ns, so timings are exact
 * and repeatable even though the throttle only keeps us roughly real-time.
 */
static unsigned long long instr_count;	/* instructions executed since reset */
static const char *stats_path;		/* --stats FILE, read by the gdb pane */

static void emit_time(avr_t *avr)
{
	fprintf(out, "time %llu %llu %lu\n", (unsigned long long)avr->cycle, instr_count,
		(unsigned long)avr->frequency);
	fflush(out);
}

/* Write the counters (see below) to the stats file. Written to a temp
 * file and renamed, so the gdb pane never reads a half-written line. */
static void write_stats(avr_t *avr, int stopped)
{
	if (!stats_path) return;
	char tmp[4096];
	snprintf(tmp, sizeof tmp, "%s.tmp", stats_path);
	FILE *f = fopen(tmp, "w");
	if (!f) return;
	/* cycles instructions freq state */
	fprintf(f, "%llu %llu %lu %s\n", (unsigned long long)avr->cycle, instr_count,
		(unsigned long)avr->frequency, stopped ? "stopped" : "running");
	fclose(f);
	rename(tmp, stats_path);
}

static void usage(const char *prog)
{
	fprintf(stderr,
		"usage: %s [options] firmware.elf\n"
		"  --mcu NAME     default atmega328p\n"
		"  --freq HZ      default 16000000\n"
		"  --gdb-port N   default 1234\n"
		"  --no-gdb       do not start the gdb server\n"
		"  --wait-gdb     start halted until gdb connects and continues\n"
		"  --speed X      real-time multiplier (default 1.0, 0 = unthrottled)\n"
		"  --stats FILE   keep cycle/instruction totals in FILE (for the gdb pane)\n",
		prog);
}

int main(int argc, char **argv)
{
	out = fdopen(dup(STDOUT_FILENO), "w");
	if (!out) return 1;
	setvbuf(out, NULL, _IOLBF, 0);
	out_fd = fileno(out);
	dup2(STDERR_FILENO, STDOUT_FILENO);	/* library chatter goes to stderr */

	const char *mcu = "atmega328p", *elf = NULL;
	long freq = 16000000;
	int gdb_port = 1234, use_gdb = 1, wait_gdb = 0;
	double speed = 1.0;

	for (int i = 1; i < argc; i++) {
		const char *a = argv[i];
		int has_arg = i + 1 < argc;
		if (!strcmp(a, "--mcu") && has_arg)           mcu = argv[++i];
		else if (!strcmp(a, "--freq") && has_arg)     freq = atol(argv[++i]);
		else if (!strcmp(a, "--gdb-port") && has_arg) gdb_port = atoi(argv[++i]);
		else if (!strcmp(a, "--speed") && has_arg)    speed = atof(argv[++i]);
		else if (!strcmp(a, "--stats") && has_arg)    stats_path = argv[++i];
		else if (!strcmp(a, "--no-gdb"))              use_gdb = 0;
		else if (!strcmp(a, "--wait-gdb"))            wait_gdb = 1;
		else if (a[0] != '-' && !elf)                 elf = a;
		else { usage(argv[0]); return 2; }
	}
	if (!elf || freq <= 0 || speed < 0) { usage(argv[0]); return 2; }

	/* Load the firmware and build the simulated chip. */
	elf_firmware_t fw;
	memset(&fw, 0, sizeof fw);
	if (elf_read_firmware(elf, &fw) != 0) {
		fprintf(stderr, "launcher: cannot read ELF '%s'\n", elf);
		return 1;
	}
	avr_t *avr = avr_make_mcu_by_name(mcu);
	if (!avr) {
		fprintf(stderr, "launcher: unknown MCU '%s'\n", mcu);
		return 1;
	}
	avr_init(avr);
	avr->frequency = (uint32_t)freq;
	avr_load_firmware(avr, &fw);

	if (use_gdb) {
		avr->gdb_port = gdb_port;
		if (wait_gdb)
			avr->state = cpu_Stopped;	/* gdb must "continue" us */
		if (avr_gdb_init(avr) != 0) {
			fprintf(stderr, "launcher: cannot start gdb server on port %d\n", gdb_port);
			return 1;
		}
		fprintf(stderr, "launcher: gdb server on port %d%s\n", gdb_port,
			wait_gdb ? " (halted, waiting for gdb)" : "");
	} else if (wait_gdb) {
		fprintf(stderr, "launcher: --wait-gdb ignored because of --no-gdb\n");
	}

	struct sigaction sa;
	memset(&sa, 0, sizeof sa);
	sa.sa_handler = on_signal;
	sigemptyset(&sa.sa_mask);
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);
	signal(SIGPIPE, SIG_IGN);	/* reader gone: fprintf fails instead of killing us */


	/*
	 * Throttle bookkeeping. We remember a wall-clock instant (base_ns) and
	 * the cycle count at that instant (base_cycle). Simulated time since then
	 * is (cycle - base_cycle) / freq; we sleep whenever it is ahead of
	 * wall time * speed. Re-baselining on resume avoids a catch-up burst.
	 */
	int64_t base_ns = now_ns(), next_io = base_ns;
	avr_cycle_count_t base_cycle = avr->cycle;
	int stopped = (avr->state == cpu_Stopped);
	unsigned counter = 0;
	const char *reason = "done";

	emit_state(stopped);
	emit_io(avr);
	emit_time(avr);
	write_stats(avr, stopped);
	int64_t next_time = base_ns;

	for (;;) {
		/* Runs one instruction, or (if stopped) serves gdb for a short while. */
		int before = avr->state;
		avr_cycle_count_t cycle_before = avr->cycle;
		int st = avr_run(avr);
		/* An instruction ran if the cycle count advanced, unless the CPU was asleep
		 * (sleeping also advances cycles). The state at entry can be "stopped": gdb's
		 * continue/stepi resume the CPU inside this same call. A breakpoint stops the
		 * CPU before its instruction runs, so it adds no cycles and isn't counted. */
		if (avr->cycle != cycle_before && before != cpu_Sleeping)
			instr_count++;
		if (st == cpu_Done)    { reason = "done";    break; }
		if (st == cpu_Crashed) { reason = "crashed"; break; }

		/* Report run/stop transitions (cpu_Step counts as running). */
		int now_stopped = (st == cpu_Stopped);
		if (now_stopped != stopped) {
			stopped = now_stopped;
			emit_io(avr);	/* flush what happened before the change */
			emit_time(avr);
			write_stats(avr, stopped);
			emit_state(stopped);
			if (!stopped) {	/* resumed: restart the real-time clock */
				base_ns = now_ns();
				base_cycle = avr->cycle;
			}
		}

		/* Cheap checks only every 2048 instructions while running. */
		if (!stopped && (++counter & 0x7ff) != 0)
			continue;

		int64_t t = now_ns();
		if (t >= next_io) {
			emit_io(avr);
			next_io = t + 10000000LL;	/* ~10 ms */
		}
		if (!stopped && t >= next_time) {
			emit_time(avr);		/* live stopwatch in the viewer */
			next_time = t + 50000000LL;	/* ~50 ms */
		}
		if (stopped) {
			/* avr_run already waited for gdb; keep the baseline fresh. */
			base_ns = t;
			base_cycle = avr->cycle;
			continue;
		}
		if (speed > 0) {
			/* Wall time at which this cycle count is "due". */
			double sim_ns = (double)(avr->cycle - base_cycle) * 1e9 / (double)freq / speed;
			int64_t ahead = base_ns + (int64_t)sim_ns - t;
			if (ahead > 2000000LL) {
				/* Sleep in <=10 ms slices so signals/io stay responsive. */
				if (ahead > 10000000LL) ahead = 10000000LL;
				sleep_ns(ahead);
			}
		}
	}

	emit_io(avr);
	emit_time(avr);
	write_stats(avr, 1);
	fprintf(out, "exit %s\n", reason);
	fflush(out);
	fprintf(stderr, "launcher: exit (%s)\n", reason);
	if (use_gdb)
		avr_deinit_gdb(avr);
	avr_terminate(avr);
	return 0;
}
