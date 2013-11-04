import gevent.monkey
gevent.monkey.patch_all()

from geventirc.irc import Client
import geventirc.handlers as handlers
from geventirc.message import Join

import gevent
from gevent.select import select

import curses
from curses.wrapper import wrapper as curses_wrapper
from curses.textpad import Textbox

import sys
from getpass import getpass
from signal import signal, SIGWINCH

from scrollpad import ScrollPad


host = 'localhost'
port = 6667
nick = 'ekimekim'
real_name = 'Mike Lang'
channels = ['#test']

scrollpad = None
textpad = None
textwin = None

NICK_HIGHLIGHT = (curses.COLOR_BLACK, curses.COLOR_RED), curses.A_STANDOUT
NICK_PAIR = 1
SENDER_WIDTH = 12
USER_WIDTH = 12

def curses_wraps(fn):
    """Decorator for curses_wrapper"""
    return lambda *args, **kwargs: curses_wrapper(fn, *args, **kwargs)

def main(*args):
	global password
	password = getpass("Password for {}: ".format(nick))
	return curses_main(*args)

@curses_wraps
def curses_main(stdscr, *args):
	global scrollpad, textpad, textwin

	curses.curs_set(0) # Cursor invisible
	if NICK_HIGHLIGHT: curses.init_pair(NICK_PAIR, *NICK_HIGHLIGHT[0])

	height, width = stdscr.getmaxyx()
	scrollpad = ScrollPad((0,0), (height-2, width))
	textwin = stdscr.subwin(height-1, 0)
	textwin.refresh()
	textpad = Textbox(textwin)

	curses_winch_handler = None
	def winch_handler(signum, frame):
		curses_winch_handler()
		gevent.spawn(_winch_handler)
	def _winch_handler():
		height, width = stdscr.getmaxyx()
		scrollpad.resize(height-1, width)
	curses_winch_handler = signal(SIGWINCH, winch_handler)

	client = Client(host, nick, port, real_name=real_name)

	client.add_handler(RespectfulNickServHandler(nick, password))
	for channel in channels:
		client.add_handler(handlers.JoinHandler(channel))
	client.add_handler(generic_recv)

	client.start()
	gevent.spawn(input_handler)
	client.join()


class IdentifiedJoinHandler(object):
	commands = ['NOTICE']
	def __init__(self, channels):
		self.channels = channels
	def __call__(self, client, msg):
		if msg.sender == 'NickServ' and msg.params and ' '.join(msg.params[1:]).startswith("You are now identified"):
			for chan in self.channels:
				client.send_message(Join(chan))


class RespectfulNickServHandler(handlers.NickServHandler):
	commands = handlers.NickServHandler.commands + ['NICK']
	def __call__(self, client, msg):
		global nick
		if msg.command == 'NICK':
			if msg.params:
				self.nick = msg.params[-1]
				out("Warning: Server forced nick change to {!r}".format(nick))
		else:
			super(RespectfulNickServHandler, self).__call__(client, msg)
		nick = self.nick


def generic_recv(client, msg):
	if msg.command == 'PRIVMSG':
		if not msg.params:
			out(msg.encode().rstrip())
			return
		speaker = msg.params[0]
		text = ' '.join(msg.params[1:])
		out("({msg.sender:{SENDER_WIDTH}}) {speaker:{USER_WIDTH}}: {text}".format(
			msg=msg, speaker=speaker, text=text, **globals()
		))
	else:
		try:
			n = int(msg.command)
		except ValueError:
			pass
		# numeric command - print as is
		out(msg.encode().rstrip())


def out(s):
	s += '\n'
	if NICK_HIGHLIGHT and nick in s:
		parts = s.split(nick)
		scrollpad.addstr(parts[0], refresh=False)
		for part in parts[1:]:
			scrollpad.addstr(nick, curses.color_pair(NICK_PAIR) | NICK_HIGHLIGHT[1], refresh=False)
			scrollpad.addstr(part, refresh=False)
		scrollpad.refresh()
	else:
		scrollpad.addstr(s)


def input_handler():
	while 1:
		while 1:
			ch = gevent_getch(sys.stdin.fileno(), textwin)
			if not ch: 
				continue
			if not textpad.do_command(ch):
				break
			textwin.refresh()
		line = textpad.gather()
		# TODO process line


def gevent_getch(fd, scr):
	r = []
	while fd not in r:
		r, w, x = select([fd], [], []) 
	return scr.getch()


if __name__=='__main__':
	sys.exit(main(*sys.argv) or 0)
