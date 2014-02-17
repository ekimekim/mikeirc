import gevent.monkey
gevent.monkey.patch_all()

from geventirc.irc import Client
import geventirc.handlers as handlers
from geventirc.message import Join, Me, Command, PrivMsg, CTCPMessage

import sys
import os
from getpass import getpass
import socket
import re
import string

from gevent.select import select
from gevent.pool import Group
from gevent.backdoor import BackdoorServer

from editing import LineEditing
from smart_reset import smart_reset
from scriptlib import with_argv

# config file should define these values.
# channel should have leading #
from config import host, port, nick, real_name, channel, email

users = set()
ops = set()

COMMAND_HIGHLIGHT = "30"
KICK_HIGHLIGHT = "35"
PRIVATE_HIGHLIGHT = "1"
NICK_HIGHLIGHT = "31;1"
USER_HIGHLIGHT = "32"
OP_HIGHLIGHT = "33"

SENDER_WIDTH = 12
USER_WIDTH = 12

USER_HIGHLIGHTS = {
	'BidServ': '1;33',
}
KEYWORD_HIGHLIGHTS = {
	nick: NICK_HIGHLIGHT, # original nick always gets highlighted
}

EXCLUDE_NUMERICS = {5}

main_greenlet = None



def read():
	import sys
	fd = sys.stdin.fileno()
	r,w,x = select([fd], [], [])
	assert fd in r
	return os.read(fd, 1)
editor = LineEditing(input_fn=read)


class ConnClosed(Exception):
	def __str__(self):
		return "Connection Closed"


@with_argv
def main(host=host, port=port, nick=nick, real_name=real_name, channel=channel, email=email, backdoor=False, debug=False):
	global main_greenlet
	global client

	password = getpass("Password for {}: ".format(nick))
	port = int(port)

	workers = Group()
	main_greenlet = gevent.getcurrent()

	if backdoor:
		backdoor = BackdoorServer(('localhost',1666))
		backdoor.start()

	# TODO GET RID OF THIS FUCKER
	globals().update(locals())

	client = None
	backoff = Backoff(1, 60, 1.5)
	while True:
		try:
			try:
				client = Client(host, nick, port, real_name=real_name, disconnect_handler=on_disconnect, debug=debug)

				client.add_handler(handlers.ping_handler, 'PING')
				client.add_handler(MyNickServHandler(nick, password, email=email))
				client.add_handler(handlers.JoinHandler(channel))
				client.add_handler(generic_recv)
				client.add_handler(UserListHandler())

				client.start()
				workers.spawn(in_worker)

				backoff.clear() # successful startup
				client.join()
			except BaseException:
				ex, ex_type, tb = sys.exc_info()
				if client: client.stop()
				workers.kill()
				raise ex, ex_type, tb
		except (socket.error, ConnClosed), ex:
			print ex
			time = backoff.get()
			print "retrying in %.2f seconds..." % time
			gevent.sleep(time)


class Backoff(object):
	def __init__(self, start, limit, rate):
		self.start = start
		self.limit = limit
		self.rate = rate
		self.clear()
	def clear(self):
		self.time = self.start
	def get(self):
		time = self.time
		self.time = min(self.limit, time * self.rate)
		return time


def on_disconnect(client):
	gevent.kill(main_greenlet, ConnClosed())
	raise gevent.GreenletExit()


normalize_patterns = r"([^|]+)|[^|]*", r"([^\[]+)\[[^\]]*\]"
normalize_patterns = [re.compile("^{}$".format(pattern)) for pattern in normalize_patterns]
def nick_normalize(nick):
	"""Lowercases and looks for forms:
	NICK|STATUS
	NICK[STATUS]
	and strips the STATUS.
	"""
	nick = nick.lower()
	for pattern in normalize_patterns:
		match = pattern.match(nick)
		if match:
			nick, = match.groups()
	return nick


class RespectfulNickServHandler(handlers.NickServHandler):
	commands = handlers.NickServHandler.commands + ['NICK']
	def __call__(self, client, msg):
		global nick
		if msg.command == 'NICK':
			try:
				target, arg = msg.params
				if target == nick:
					self.nick = msg.params[-1]
					out("Warning: Server forced nick change to {!r}".format(nick))
			except ValueError:
				pass
		else:
			super(RespectfulNickServHandler, self).__call__(client, msg)
		nick = self.nick

class EmailNickServHandler(handlers.NickServHandler):
	def __init__(self, *args, **kwargs):
		self.email = kwargs.pop('email', None)
		super(EmailNickServHandler, self).__init__(*args, **kwargs)
	def authenticate(self, client):
		if self.email:
			auth_msg = 'identify %s %s' % (self.email, self.password)
		else:
			auth_msg = 'identify %s' % self.password
		client.msg('nickserv', auth_msg)

class MyNickServHandler(RespectfulNickServHandler, EmailNickServHandler):
	pass


class UserListHandler():
	commands = ["353", "JOIN", "PART", "QUIT", "MODE", "NICK"]

	def __call__(self, client, msg):
		if msg.command == '353':
			params = msg.params[2:]
			for user in params:
				user_normalized = nick_normalize(user.lstrip('~+'))
				users.add(user_normalized)
				if user.startswith('~'): ops.add(user_normalized)
		elif msg.command == 'JOIN':
			users.add(nick_normalize(msg.sender))
		elif msg.command == 'MODE':
			flags, user = msg.params[1:3]
			flags.lstrip("+")
			if any(x in flags for x in 'aoq'):
				ops.add(nick_normalize(user))
		elif msg.command in ('PART', 'QUIT'):
			sender = nick_normalize(msg.sender)
			if sender in users: users.remove(sender)
			if sender in ops: ops.remove(sender)
		elif msg.command == 'NICK':
			sender = nick_normalize(msg.sender)
			new_nick = nick_normalize(msg.params[0])
			users.add(new_nick)
			if sender in users: users.remove(sender)
			if sender in ops:
				ops.add(new_nick)
				ops.remove(sender)
		else:
			assert False
#		out("DEBUG: |users| = {}, |ops| = {}".format(len(users),len(ops)))


def generic_recv(client, msg, sender=None):

	target = msg.params[0]
	text = ' '.join(msg.params[1:])
	if not sender: sender = msg.sender
	is_action = False

	highlight = lambda outstr, sequence: '\x1b[{}m{}\x1b[m'.format(sequence, outstr)

	# default outstr
	outstr = highlight("{sender:>{SENDER_WIDTH}}: {target} {msg.command} {text}", COMMAND_HIGHLIGHT)

	if msg.command == 'PRIVMSG':
		if not msg.params:
			# bad message
			out(msg.encode().rstrip())
			return

		if isinstance(msg, CTCPMessage):
			for param in msg.ctcp_params:
				if param and param[0] == 'ACTION':
					is_action = True
					text = param[1]

		if target == channel:
			if is_action:
				outstr = "{sender:>{SENDER_WIDTH}} {text}"
			else:
				outstr = "{sender:>{SENDER_WIDTH}}: {text}"
			if sender in USER_HIGHLIGHTS:
				outstr = highlight(outstr, USER_HIGHLIGHTS[sender])
		else:
			# private message
			sender = "[{}]".format(sender)
			if target != nick:
				text = '[{}] {}'.format(target, text)
			outstr = highlight("{sender:>{SENDER_WIDTH}}: {text}", PRIVATE_HIGHLIGHT)
	elif msg.command == 'QUIT':
		outstr = highlight("{sender:>{SENDER_WIDTH}} quits: {text}", COMMAND_HIGHLIGHT)
	elif msg.command == 'NICK':
		outstr = highlight("{sender:>{SENDER_WIDTH}} changes their name to {target}", COMMAND_HIGHLIGHT)
	elif msg.command == 'KICK':
		empty = ''
		target, text = text.split(' ', 1)
		outstr = highlight("{empty:>{SENDER_WIDTH}} {target} kicked by {sender}: {text}", KICK_HIGHLIGHT)
	elif msg.command == 'PING':
		return
	else:
		try:
			n = int(msg.command, 10)
		except ValueError:
			# unknown message type
			pass
		else:
			# numeric command - unless excluded, print
			if n in EXCLUDE_NUMERICS: return
			if sender == host and target == nick:
				outstr = highlight("{msg.command:>{SENDER_WIDTH}}: {text}", COMMAND_HIGHLIGHT)
			else:
				# not sure what circumstances this would apply for, use default
				pass
	d = globals().copy()
	d.update(locals())
	out(outstr.format(**d))


def out(s):
	# highlight nick
	keywords = {}
	keywords.update({nick: NICK_HIGHLIGHT})
	keywords.update({user: USER_HIGHLIGHT for user in users})
	keywords.update({user: OP_HIGHLIGHT for user in ops})
	keywords.update(KEYWORD_HIGHLIGHTS)
	keywords = {k.lower(): v for k, v in keywords.items()}

	outbuf = ''
	buf = ''
	in_escape = False
	for c in s + '\0': # add terminator to ensure final buf contents get flushed
		if c in string.letters + string.digits + '_-' and not in_escape:
			buf += c
		else:
			if buf.lower() in keywords and not in_escape:
				outbuf += '\x1b[{}m{}\x1b[m'.format(keywords[buf.lower()], buf)
			else:
				outbuf += buf
			outbuf += c
			if in_escape and c == 'm':
				in_escape = False
			buf = ''
			if outbuf.endswith('\x1b['):
				in_escape = True
	outbuf = outbuf[:-1] # remove terminator
	editor.write(smart_reset(outbuf))


def in_worker():
	fd = sys.stdin.fileno()
	with editor:
		try:
			# TODO echo my own commands
			while True:
				line = editor.readline()
				if line:
					cmd = None
					if not line.startswith('/'):
						message = PrivMsg(channel, line)
					else:
						line = line[1:]
						cmd, line = line.split(' ', 1)
						if cmd == 'me':
							message = Me(channel, line)
						else:
							message = Command(line.split(), command=cmd)
					client.send_message(message)
					generic_recv(client, message, sender=nick)
					if cmd == 'quit':
						sys.exit()
		except EOFError:
			sys.exit()

if __name__=='__main__':
	main()
