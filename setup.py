from setuptools import setup, find_packages

setup(
	name="mikeirc",
	version="0.0.1",
	author="ekimekim",
	author_email="mikelang3000@gmail.com",
	description="single-channel terminal irc client",
	packages=find_packages(),
	install_requires=[
		'gevent',
		'geventirc',
	],
)
