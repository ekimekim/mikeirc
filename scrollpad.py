import curses
import logging

class ScrollPad(object):
	def __init__(self, screen_pos, size, scroll_limit=32000):
		self.height, self.width = size
		self.screen_pos = screen_pos
		self.scroll_limit = scroll_limit
		self.pad = curses.newpad(scroll_limit, self.width)
		self.replay = []
		logging.info("Created pad with size %s", self.pad.getmaxyx())

	def addstr(self, s, attr=None, refresh=True, replay=True):
		if replay:
			self.replay.append((s, attr))
			self.replay = self.replay[-512:]
		if attr:
			self.pad.addstr(s, attr)
		else:
			self.pad.addstr(s)

		if refresh:
			self.refresh()

	def refresh(self):
		screen_y, screen_x = self.screen_pos
		y, x = self.pad.getyx()
		if float(y)/self.scroll_limit >= 0.9:
			self.resize(self.width, self.height)
			y, x = self.pad.getyx()
		pos = max(0, y - self.height)
		self.pad.refresh(pos, 0, screen_y, screen_x, screen_y + self.height, screen_x + self.width)

	def resize(self, height, width):
		self.width, self.height = width, height
		self.pad = curses.newpad(self.scroll_limit, self.width)
		for s, attr in self.replay:
			self.addstr(s, attr, refresh=False, replay=False)
