import gevent
import gevent.select
import itertools as it

from withtermios import TermAttrs
from editing import LineEditing
import escapes

import sys

def input_fn():
	r, w, x = gevent.select.select([sys.stdin], [], [])
	assert sys.stdin in r
	return sys.stdin.read(1)

def input_loop(editor):
	with editor:
		x = ''
		try:
			while x != 'exit':
				x = editor.readline()
				print repr(x)
		except EOFError:
			pass

def spam(editor):
	for n in it.count():
		gevent.sleep(1)
		editor.write("Blah blah %d" % n)

editor = LineEditing(input_fn=input_fn)
gevent.spawn(spam, editor)
input_loop(editor)
print
