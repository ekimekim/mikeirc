
import time
import logging

import gevent
import requests

API = "https://pronouns.alejo.io/api/users"

class Pronouns(object):
	def __init__(self, ttl=600, negative_ttl=300, sync=False):
		"""If sync=True, block if needed on get. Otherwise return immediately
		with (possibly stale) existing value or 'unknown' and refresh in background."""
		self.cache = {} # {user: (time retrieved, pronouns or None)}
		self.fetching = {}
		self.ttl = ttl
		self.negative_ttl = negative_ttl
		self.sync = sync

	def get(self, user):
		user = user.lower()
		now = time.time()
		if user in self.cache:
			when, result = self.cache[user]
			ttl = self.negative_ttl if result is None else self.ttl
			if now <= when + ttl:
				return result
		if self.sync:
			return self.fetch(now, user)
		if user not in self.fetching:
			self.fetching[user] = gevent.spawn(self.fetch, now, user)
		return self.cache.get(user, (None, 'unknown'))[1]

	def fetch(self, now, user):
		try:
			resp = requests.get("{}/{}".format(API, user))
			resp.raise_for_status()
			result = resp.json()
			if result:
				pronouns = result[0]["pronoun_id"]
			else:
				pronouns = None
			self.cache[user] = now, pronouns
			return pronouns
		except Exception:
			logging.debug("Failed to fetch pronouns", exc_info=True)
		finally:
			self.fetching.pop(user)
