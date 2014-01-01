import sys
import string

import escapes
from withtermios import TermAttrs
from common import instanceclass


escape_actions = {}
history = []
history_pos = 0

def get_termattrs(fd=0, **kwargs):
	"""Return the TermAttrs object to use. Passes args to term attrs.
	Note it is a modification of the termios settings at the time of this call.
	"""
	# we don't really want full raw mode, just use what's already set with just enough
	# for what we want
	import termios as t
	return TermAttrs.modify(
		(t.IGNPAR|t.ICRNL, 0, 0, 0),
		(0, 0, 0, t.ECHO|t.ICANON|t.IEXTEN),
		fd=fd, **kwargs
	)

@instanceclass
class HiddenCursor(object):
	"""Context manager that hides the cursor.
	Assumes sys.stdout is a tty.
	"""
	def __enter__(self):
		sys.stdout.write('\x1b[?25l')
	def __exit__(self, *exc_info):
		sys.stdout.write('\x1b[?25h')


def readline(file=sys.stdin, input_fn=None, output=sys.stdout):
	"""Reads a line of input with line editing.
	Takes either a file, or an input function, which
	should behave like file.read(1) (ie. no input means closed).
	It may also raise EOFError directly.
	Returns after reading a newline, or an EOF.
	If no text was written and EOF was recieved, raises EOFError,
	otherwise returns ''.
	"""

	if not input_fn: input_fn = lambda: file.read(1)

	TERMINATORS = {'\r', '\n'}

	head, tail = '', ''
	esc_buf = ''

	global history_pos
	history.insert(0, '')
	history_pos = 0

	try:
		while True:

			display(head, tail, output)

			# read input
			c = input_fn()
			#open('/tmp/log','a').write('{!r}\n'.format(c)) # for debug
			if not c:
				raise EOFError()
			if c in TERMINATORS:
				break
			esc_buf += c

			# check for full escape sequence
			if esc_buf in escape_actions:
				head, tail = escape_actions[esc_buf](head, tail)
				esc_buf = ''

			# on partial escape sequences, continue without action
			if any(sequence.startswith(esc_buf) for sequence in escape_actions):
				continue

			# filter non-printing chars before we add to main buffer
			esc_buf = filter(lambda c: c in string.printable, esc_buf)

			# flush escape buffer
			head += esc_buf
			esc_buf = ''

	except KeyboardInterrupt:
		head = tail = ''
		# fall through
	except EOFError:
		if not (head or tail): raise
		# fall through

	history[0] = head + tail
	if not history[0]: history.pop(0)
	return head + tail


def display(head, tail, output):
	if not tail: tail = ' '
	output.write(
		  escapes.SAVE_CURSOR
		+ escapes.SET_CURSOR(1,999)
		+ escapes.CLEAR_LINE
		+ head
		+ escapes.INVERTCOLOURS + tail[0] + escapes.UNFORMAT
		+ tail[1:]
		+ escapes.LOAD_CURSOR
	)


def escape(*matches):
	def _escape(fn):
		global escape_actions
		for match in matches:
			escape_actions[match] = fn
		return fn
	return _escape


@escape('\x1b[D')
def left(head, tail):
	if not head: return head, tail
	return head[:-1], head[-1] + tail

@escape('\x1b[C')
def right(head, tail):
	if not tail: return head, tail
	return head + tail[0], tail[1:]

@escape('\x7f')
def backspace(head, tail):
	return head[:-1], tail

@escape('\x1b[3~')
def delete(head, tail):
	return head, tail[1:]

@escape('\x1bOH')
def home(head, tail):
	return '', head+tail

@escape('\x1bOF')
def home(head, tail):
	return head+tail, ''

@escape('\04') # ^D
def eof(head, tail):
	raise EOFError()


# history
@escape('\x1b[A')
def up(head, tail):
	global history_pos
	open('/tmp/log', 'a').write('{!r}[{}]\n'.format(history, history_pos))
	if history_pos >= len(history) - 1:
		return head, tail
	if history_pos == 0:
		history[0] = head + tail
	history_pos += 1
	return history[history_pos], ''

@escape('\x1b[B')
def down(head, tail):
	global history_pos
	if history_pos <= 0:
		return head, tail
	history_pos -= 1
	return history[history_pos], ''

