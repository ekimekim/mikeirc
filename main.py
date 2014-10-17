import gevent.monkey
gevent.monkey.patch_all()

from geventirc.irc import Client
import geventirc.handlers as handlers
from geventirc.message import Me, Command, PrivMsg, CTCPMessage

import sys
import os
from getpass import getpass
import socket
import re
import string

from gevent.select import select
from gevent.pool import Group
from gevent.backdoor import BackdoorServer

from lineedit import LineEditing
from pyconfig import Config

from smart_reset import smart_reset
from scriptlib import with_argv

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
	'ekimekim': NICK_HIGHLIGHT, # original nick always gets highlighted
}

EXCLUDE_NUMERICS = {5}

IGNORE_NICKS = {"fbt"}

main_greenlet = None



def read():
	fd = sys.stdin.fileno()
	r,w,x = select([fd], [], [])
	assert fd in r
	return os.read(fd, 1)
editor = LineEditing(input_fn=read)


class ConnClosed(Exception):
	def __str__(self):
		return "Connection Closed"


def main():

	config = Config()
	# loads from the default config file, then argv and env. setting --conf allows you
	# to specify a conf file at "argv level" priority, overriding the defaults.
	config.load_all(file_path=os.path.join(os.path.dirname(__file__), 'config/defaults'))

	# this is horrible
	# required keys
	host = config['host']
	channel = config['channel']
	nick = config['nick']
	# optional keys. note that defaults are None (which works as False)
	port = config.port or 6667
	real_name = config.real_name or nick
	email = config.email
	backdoor = config.backdoor
	debug = config.debug
	twitch = config.twitch
	quiet = config.quiet
	no_email = (email is None)
	no_auth = config.no_auth
	password = config.password

	global main_greenlet
	global client

	if password is None and not no_auth:
		password = getpass("Password for {}: ".format(nick))
	if not password:
		no_auth = True
	port = int(port)

	workers = Group()
	main_greenlet = gevent.getcurrent()

	if backdoor:
		backdoor = BackdoorServer(('localhost',1666))
		backdoor.start()

	if twitch:
		if not isinstance(twitch, basestring):
			host = 'irc.twitch.tv'
		else:
			host = twitch

	# TODO GET RID OF THIS FUCKER
	globals().update(locals())

	client = None
	backoff = Backoff(1, 5, 1.5)
	while True:
		try:
			try:
				client = Client(host, nick, port, real_name=real_name, disconnect_handler=on_disconnect, twitch=twitch, password=password)

				client.add_handler(handlers.ping_handler, 'PING')
				if not twitch:
					handler = get_nick_handler(no_auth=no_auth, with_email=(not no_email))
					kwargs = dict(email=email) if not (no_email or no_auth) else {}
					client.add_handler(handler(nick, password, **kwargs))
				client.add_handler(handlers.JoinHandler(channel))
				client.add_handler(generic_recv)
				client.add_handler(UserListHandler())

				client.start()
				workers.spawn(in_worker)
				workers.spawn(pinger)

				backoff.clear() # successful startup
				client.join()
			except BaseException:
				ex, ex_type, tb = sys.exc_info()
				if client:
					try: client.stop()
					except: pass
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
		nick = self.current_nick or self.nick

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

class CommandNickServHandler(handlers.NickServHandler):
	def authenticate(self, client):
		client.send_message(Command([self.password], command='PASS'))

class NoAuthNickHandler(handlers.NickServHandler):
	def authenticate(self, client):
		pass

def get_nick_handler(no_auth=False, with_email=True):
	handler = handlers.NickServHandler
	if with_email:
		handler = EmailNickServHandler
	if no_auth:
		handler = NoAuthNickHandler
	class MyNickServHandler(RespectfulNickServHandler, handler):
		pass
	return MyNickServHandler


class UserListHandler():
	commands = ["353", "JOIN", "PART", "QUIT", "MODE", "NICK"]

	def __call__(self, client, msg):
		if msg.command == '353':
			params = msg.params[2:]
			for user in params:
				user_normalized = nick_normalize(user.lstrip('@~+'))
				users.add(user_normalized)
				if user.startswith('@~&'): ops.add(user_normalized)
		elif msg.command == 'JOIN':
			users.add(nick_normalize(msg.sender))
		elif msg.command == 'MODE':
			if len(msg.params) < 3:
				return
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

	params = msg.params
	text = ' '.join(msg.params)
	if not sender: sender = msg.sender
	is_action = False

	if sender in IGNORE_NICKS:
		return

	highlight = lambda outstr, sequence: '\x1b[{}m{}\x1b[m'.format(sequence, outstr)

	# default outstr
	outstr = highlight("{sender:>{SENDER_WIDTH}}: {msg.command} {text}", COMMAND_HIGHLIGHT)

	nosend = False

	if msg.command == 'PRIVMSG':
		target, text = params[0], ' '.join(params[1:])

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
			if is_action:
				outstr = highlight("{sender:>{SENDER_WIDTH}} {text}", PRIVATE_HIGHLIGHT)
			else:
				outstr = highlight("{sender:>{SENDER_WIDTH}}: {text}", PRIVATE_HIGHLIGHT)
	elif msg.command == 'QUIT':
		outstr = highlight("{sender:>{SENDER_WIDTH}} quits: {text}", COMMAND_HIGHLIGHT)
		if quiet: nosend = True
	elif msg.command == 'NICK':
		target, text = params[0], ' '.join(params[1:])
		outstr = highlight("{sender:>{SENDER_WIDTH}} changes their name to {target}", COMMAND_HIGHLIGHT)
		if quiet: nosend = True
	elif msg.command == 'KICK':
		chan, target, text = params[0], params[1], ' '.join(params[2:])
		empty = ''
		outstr = highlight("{empty:>{SENDER_WIDTH}} {target} kicked by {sender}: {text}", KICK_HIGHLIGHT)
	elif msg.command == 'PING':
		return
	else:
		if quiet: nosend = True
		try:
			n = int(msg.command, 10)
		except ValueError:
			# unknown message type
			pass
		else:
			# numeric command - unless excluded, print
			if n in EXCLUDE_NUMERICS: return
			if sender == host and params and params[0] == nick:
				outstr = highlight("{msg.command:>{SENDER_WIDTH}}: {text}", COMMAND_HIGHLIGHT)
			else:
				# not sure what circumstances this would apply for, use default
				pass
	d = globals().copy()
	d.update(locals())
	if not nosend:
		out(outstr.format(**d))


def out(s):
	# highlight nick
	keywords = {}
	keywords.update({user: USER_HIGHLIGHT for user in users})
	keywords.update({user: OP_HIGHLIGHT for user in ops})
	keywords.update({nick: NICK_HIGHLIGHT})
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
	with editor:
		try:
			while True:
				line = editor.readline()
				if line:
					cmd = None
					def process_esc(match):
						num, = match.groups()
						return chr(int(num, 16))
					line = line.replace(r'\\', '\\')
					line = re.sub(r"\\x([0-9a-fA-F]{2})", process_esc, line)
					if not line.startswith('/'):
						message = PrivMsg(channel, line)
					else:
						args = line[1:].split()
						line = lambda: ' '.join(args)
						cmd = args.pop(0)
						if not cmd:
							# "/ TEXT" -> literal privmsg "/TEXT"
							message = PrivMsg(channel, '/' + line())
						elif cmd == 'me':
							message = Me(channel, line())
						elif cmd in ('msg', 'memsg'):
							message_type = Me if cmd == 'memsg' else PrivMsg
							if not args:
								# XXX consider displaying an error msg?
								continue
							target = args.pop(0)
							message = message_type(target, line())
						elif cmd == 'localexec':
							scope = {}
							exec line() in globals(), scope
							message = scope.get('message', None)
						else:
							message = Command(args, command=cmd)
					if message:
						client.send_message(message)
						generic_recv(client, message, sender=nick)
					# post actions
					if cmd == 'quit':
						sys.exit()
					elif cmd == 'nick' and args:
						global nick
						nick = args[0]
		except EOFError:
			sys.exit()

def pinger():
	"""We don't even care about getting a response, just make sure we're constantly writing to the socket,
	otherwise we may not notice if it dies."""
	while True:
		gevent.sleep(60)
		client.send_message(Command(["autoping"], command='PING'))

if __name__=='__main__':
	main()
