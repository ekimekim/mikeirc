
import gevent.monkey
gevent.monkey.patch_all()

from collections import Counter, defaultdict
import itertools
import logging
import os
import sys
import time

import argh
import gevent.lock

from girc import Client
import escapes
import lineedit
import termhelpers


output_lock = gevent.lock.RLock()
window = []

# config
times = [(1, '1s'), (10, '10s'), (60, '1m'), (300, '5m')]
max_time = max(t for t, s in times)
hist_time = 20
hist_min_msgs = 5
hist_min_count = 2
hist_min_force_count = 5
hist_fold_case = True
cols, _ = termhelpers.termsize()
display_interval = 0.1
smooth_unicode_hist = False


class ExplicitlyBuffered(object):
	def __init__(self, fileobj):
		self.fileobj = fileobj
		self.buf = ''

	def write(self, s):
		self.buf += s

	def flush(self):
		buf = self.buf
		self.buf = ''
		self.fileobj.write(buf)
		self.fileobj.flush()

	def fileno(self):
		return self.fileobj.fileno()


def main(channel, user, oauth_file, log_level='WARNING'):
	logging.basicConfig(level=log_level)
	sys.stdout = ExplicitlyBuffered(sys.stdout)

	with open(oauth_file) as f:
		password = f.read().strip()

	client = Client('irc.chat.twitch.tv', user, password=password, twitch=True)
	client.channel(channel).join()

	@client.handler(command='PRIVMSG')
	def recv(client, msg):
		now = time.time()
		window.append((now, msg.payload))

		payload = msg.payload
		if len(payload) > cols - 1:
			payload = payload[:cols-3] + '...'

		with output_lock:
			sys.stdout.write('\n')
			display(now)
			sys.stdout.write(payload)
			sys.stdout.flush()

	client.start()
	gevent.spawn(display_loop)

	try:
		with lineedit.HiddenCursor():
			client.join()
	finally:
		sys.stdout.flush() # explicit flush for unhide cursor


def unicode_hist(series, length):
	# series is a generator, only take as much as we need
	series = [item for i, item in zip(range(length), series)]
	ceiling = max(series)
	if ceiling == 0:
		ceiling = 1
	normalized = [float(item) / ceiling for item in series]
	quantized = [int(item * 8 + 0.5) for item in normalized]
	char_map = [u' '] + [unichr(0x2581 + i) for i in range(8)]
	rendered = u''.join(char_map[item] for item in quantized)
	return rendered.encode('utf-8')


def display(now):
	global window
	lines = []

	rates = []
	for ago, t_str in times:
		t_window = [t for t, s in window if t > now - ago]
		rate = float(len(t_window)) / ago
		rates.append('{}:{:4.1f}/s'.format(t_str, rate))
	rates = ' '.join(rates)
	def predicate(now, secs, t):
		if smooth_unicode_hist:
			return now - secs - 1 < t <= now - secs
		else:
			return int(t) == int(now - secs)
	rates += ' ' + unicode_hist(
		(len([t for t, s in window if predicate(now, secs, t)]) for secs in itertools.count()),
		cols - len(rates + ' '),
	)
	window = [(t, s) for t, s in window if t > now - max_time]
	lines += [rates, '']

	msgs = [s for t, s in window if t > now - hist_time]
	if hist_fold_case:
		folds = defaultdict(Counter)
		for msg in msgs:
			folds[msg.lower()].update([msg])
		msgs = {c.most_common(1)[0][0]: sum(c.values()) for c in folds.values()}
	else:
		msgs = Counter(s for t, s in window if t > now - hist_time)
	msgs = [(count, "{:<4d}{}".format(count, s[:cols-4])) for s, count in msgs.items() if count >= hist_min_count]
	msgs.sort(reverse=True)
	msgs_force = [s for count, s in msgs if count >= hist_min_force_count]
	msgs = [s for count, s in msgs if count < hist_min_force_count]
	msgs = msgs_force + msgs[:max(hist_min_msgs - len(msgs_force), 0)]
	lines += msgs

	lines += ['']

	with output_lock:
		sys.stdout.write(
			escapes.SAVE_CURSOR +
			escapes.set_cursor(0,0) +
			escapes.CLEAR_LINE +
			('\n' + escapes.CLEAR_LINE).join(lines) +
			escapes.LOAD_CURSOR
		)

def display_loop():
	while True:
		gevent.sleep(display_interval)
		with output_lock:
			display(time.time())
			sys.stdout.flush()

if __name__ == '__main__':
	argh.dispatch_command(main)
