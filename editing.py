import sys

import escapes


escape_actions = {}


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

	try:
		while True:

			display(head, tail, output)

			# read input
			c = input_fn()
			open('/tmp/log','a').write('{!r}\n'.format(c))
			if not c:
				raise EOFError()
			if c in TERMINATORS:
				break
			esc_buf += c

			# check for full escape sequence
			if esc_buf in escape_actions:
				head, tail = escape_actions[esc_buf](head, tail)
				esc_buf = ''
				continue
				
			# on partial escape sequences, continue without action
			if any(sequence.startswith(esc_buf) for sequence in escape_actions):
				continue

			# flush escape buffer
			head += esc_buf
			esc_buf = ''

	except EOFError:
		if not (head or tail): raise
		# fall through

	return head + tail


def display(head, tail, output):
	if not tail: tail = ' '
	output.write(escapes.SET_CURSOR(1,999))
	output.write(escapes.CLEAR_LINE)
	output.write(head)
	output.write(escapes.INVERTCOLOURS + tail[0] + escapes.UNFORMAT)
	output.write(tail[1:])


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
