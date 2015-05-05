import re

# for non-color formats, maps character to SGR escape string
FORMAT_MAP = {
	'\x02': '1', # bold
	'\x1f': '4', # underline
	'\x16': '7', # reverse fore/background
	'\x1d': None, # italic - not supported
	'\x0f': '', # reset
}

# maps colors in color format specifiers to SGR colors 0-7
# note this is a lossy conversion
COLOR_MAP = {
	0: '7', # white
	1: '0', # black
	2: '4', # blue (navy)
	3: '2', # green
	4: '1', # red
	5: '3', # brown (maroon)
	6: '5', # purple
	7: '3', # orange (olive)
	8: '3', # yellow
	9: '2', # light green (lime)
	10: '6', # teal (a green/blue cyan)
	11: '6', # light cyan (cyan) (aqua)
	12: '6', # light blue (royal)
	13: '5', # pink (light purple) (fuchsia)
	14: '0', # grey
	15: '7', # light grey (silver)
}
COLOR_CHAR = '\x03'


def apply_irc_formatting(line):
	eat = lambda s, n: (s[:n], s[n:])
	output = ''
	while line:
		c, line = eat(line, 1)
		if c in FORMAT_MAP:
			if FORMAT_MAP[c] is not None:
				output += '\x1b[{}m'.format(FORMAT_MAP[c])
		elif c == COLOR_CHAR:
			match = re.match('([0-9]{1,2})(?:,([0-9]{1,2}))?(.*)', line)
			if match:
				fore, back, line = match.groups()
				colors = '3' + COLOR_MAP.get(int(fore), '9') # default to reset for out of range
				if back is not None:
					colors += ';4' + COLOR_MAP.get(int(back), '9')
			else:
				colors = '39;49'
			output += '\x1b[{}m'.format(colors)
		else:
			output += c
	return output
