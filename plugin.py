# -*- coding: utf-8 -*-

# ##
# Copyright (c) 2014-2015, buckket
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice,
# this list of conditions, and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import re
import math
import time

import supybot.conf as conf
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.schedule as schedule
import supybot.ircmsgs as ircmsgs
import supybot.world as world


# This plugin requires the Seen plugin to be loaded.
# We're using it to calculate active users per channel and to implement an inactivity protection.
# Beware: In order to work this plugin will manually call flush() each time a vote is initiated.
from supybot.plugins.Seen.plugin import SeenDB
seen_filename = conf.supybot.directories.data.dirize('Seen.db')


class Voting(object):

    def __init__(self, channel, target, initiator, threshold):
        self.channel = channel
        self.target = target
        self.initiator = initiator
        self.threshold = threshold

        self.time = time.time()
        self.votes = []

    def remaining_time(self, voting_timeout):
        return (self.time + voting_timeout) - time.time()

    def add_vote(self, nick):
        if nick not in self.votes:
            self.votes.append(nick)
            return True
        else:
            return False

    def remove_vote(self, nick):
        if nick in self.votes:
            self.votes.remove(nick)
            return True
        else:
            return False

    def count_votes(self, users):
        def determine(nick):
            if nick in users:
                return True
            else:
                return False

        # check: http://stackoverflow.com/a/1208792
        self.votes[:] = [nick for nick in self.votes if determine(nick)]
        return len(self.votes)


class Scherbengericht(callbacks.Plugin):
    """Dieses Plugin ermöglicht die bequeme Einberufung eines Scherbengerichts.
    Benutzung auf eigene Gefahr. Für Schäden haftet der Verbraucher.

    Probieren sie auch: Nudelgericht.py
    """

    def __init__(self, irc):
        self.__parent = super(Scherbengericht, self)
        self.__parent.__init__(irc)

        # yo dawg, this is mah special irc nick reg exp combined with WEGBUXEN (change the latter if you so desire)
        self.regexp = re.compile(r"\A([a-zA-Z_\-\[\]\\^{}|`][a-zA-Z0-9_\-\[\]\\^{}|`]*) wegbuxen!?")

        self.running_votes = {}
        self.recently_joined = []

    @staticmethod
    def _calculate_id(target, channel):
        return "%s@%s" % (target, channel)

    @staticmethod
    def _split_id(id):
        return id.split("@")

    @staticmethod
    def _can_be_kicked(irc, channel, target):
        if target in irc.state.channels[channel].users and target not in irc.state.channels[channel].ops:
            return True
        else:
            return False

    def _remove_kebab(self, irc, channel, target):
        prefix = irc.state.nickToHostmask(target)
        host = ircutils.hostFromHostmask(prefix)
        hostmask = "*!*@%s" % host

        irc.queueMsg(ircmsgs.ban(channel, hostmask))
        if target in irc.state.channels[channel].users:
            irc.queueMsg(ircmsgs.kick(channel, target, "Das Volk hat entschieden."))

        def unban():
            irc.queueMsg(ircmsgs.unban(channel, hostmask))

        schedule.addEvent(unban, time.time() + int(self.registryValue("ban_duration")))

    def _is_voting_enabled(self, irc, msg, reply=False):
        channel = msg.args[0]
        if irc.isChannel(channel) and self.registryValue("gerichtsbarkeit", channel):
            return True
        else:
            if reply:
                if irc.isChannel(channel):
                    irc.reply("Ein Scherbengericht ist in %s nicht gestattet!" % channel)
                else:
                    irc.reply("Ein Scherbengericht ist stets öffentlich zu führen!")
            return False

    def _check_privileges(self, irc, msg, reply=False):
        channel = msg.args[0]
        if irc.nick not in irc.state.channels[channel].ops:
            if reply:
                irc.reply("%s braucht op ;_;" % irc.nick)
            return False
        if self._calculate_id(msg.nick, channel) in self.recently_joined:
            if reply:
                irc.queueMsg(ircmsgs.kick(channel, msg.nick, "Du warst noch nicht lange genug anwesend um abzustimmen!"))
                self._user_left(irc, msg.nick, channel)
            return False
        return True

    def _calculate_active_user(self, irc, msg):
        channel = msg.args[0]
        voting_active_time = int(self.registryValue("voting_active_time"))

        world.flush()
        seen_db = SeenDB(seen_filename)

        active_users = []
        for nick in irc.state.channels[channel].users:
            if nick not in self.recently_joined:
                try:
                    results = [[nick, seen_db.seen(channel, nick)]]
                    if len(results) == 1:
                        (nick, info) = results[0]
                        (when, said) = info
                        if (time.time() - when) <= voting_active_time:
                            active_users.append(nick)
                except KeyError:
                    pass

        return active_users

    def _calculate_voting_threshold(self, irc, msg, active_users=None):
        voting_min = int(self.registryValue("voting_min"))
        voting_quota = float(self.registryValue("voting_quota"))

        if not active_users:
            active_users = self._calculate_active_user(irc, msg)

        threshold = math.ceil(len(active_users) * voting_quota)
        return threshold if threshold > voting_min else voting_min

    def wahlrecht(self, irc, msg, args):
        """

        Listet alle Benutzer auf, die das Wahlalter noch nicht erreicht haben.
        """
        if self._is_voting_enabled(irc, msg, reply=True):
            user_list = []
            for join_id in self.recently_joined:
                (nick, channel) = self._split_id(join_id)
                if channel == msg.args[0]:
                    user_list.append(nick)
            if user_list:
                irc.reply("Folgende Mitbürger dürfen leider noch nicht abstimmen: %s" % ", ".join(user_list))
            else:
                irc.reply("Alle anwesenden Mitbürger dürfen abstimmen.")

    def schwellwert(self, irc, msg, args):
        """

        Zeigt den momentanen Schwellwert und die Anzahl der aktiven Benutzer.
        """
        if self._is_voting_enabled(irc, msg, reply=True):
            active_users = self._calculate_active_user(irc, msg)
            voting_threshold = self._calculate_voting_threshold(irc, msg, active_users)
            voting_quota = float(self.registryValue("voting_quota"))

            irc.reply("Der Schwellwert liegt momentan bei %d Stimmen (%d aktive User - Quota: %s)" % (voting_threshold, len(active_users), voting_quota))

    def abstimmungen(self, irc, msg, args):
        """

        Listet alle laufenden Abstimmungen auf.
        """
        if self._is_voting_enabled(irc, msg, reply=True):
            channel = msg.args[0]
            users = irc.state.channels[channel].users
            voting_timeout = int(self.registryValue("voting_timeout"))

            votes = []
            for voting_id in self.running_votes:
                voting = self.running_votes[voting_id]
                votes.append("[ Abstimmung gegen %s (%d von %d Stimmen) noch %d Sekunden ]" % (
                    voting.target,
                    voting.count_votes(users),
                    voting.threshold,
                    voting.remaining_time(voting_timeout)))
            if votes:
                irc.reply(", ".join(votes))
            else:
                irc.reply("Momentan laufen keine Abstimmungen.")

    def _gegen(self, irc, msg, target):
        if self._is_voting_enabled(irc, msg, reply=True) and self._check_privileges(irc, msg, reply=True):

            channel = msg.args[0]
            users = irc.state.channels[channel].users
            voting_id = self._calculate_id(target, channel)

            if target == msg.nick or target == irc.nick:
                if self._can_be_kicked(irc, channel, msg.nick):
                    irc.queueMsg(ircmsgs.kick(channel, msg.nick, "Snibeti snab XDD"))
                    self._user_left(irc, msg.nick, channel)
                    return

            if voting_id in self.running_votes:
                voting = self.running_votes[voting_id]
                if voting.add_vote(msg.nick):
                    voting_count = voting.count_votes(users)
                    if voting_count >= voting.threshold:
                        if target in irc.state.channels[channel].ops:
                            irc.queueMsg(ircmsgs.notice(channel, "Einen Versuch war's wert! :--D"))
                            for nick in voting.votes:
                                if self._can_be_kicked(irc, channel, nick):
                                    irc.queueMsg(ircmsgs.kick(channel, nick, "Bis zum nächsten mal!"))
                                    self._user_left(irc, nick, channel)
                        else:
                            self._remove_kebab(irc, channel, target)
                        del self.running_votes[voting_id]
                    else:
                        irc.reply("Stimme gegen %s registriert. Braucht noch %d weitere Stimme(n)." % (
                            target, voting.threshold - voting_count))
                else:
                    voting_count = voting.count_votes(users)
                    irc.reply("Du hast bereits gegen %s gestimmt! Braucht noch %d weitere Stimme(n)." % (
                        target, voting.threshold - voting_count))

            else:
                active_users = self._calculate_active_user(irc, msg)

                if target not in active_users:
                    irc.reply("%s ist inaktiv. Antrag abgelehnt." % target)
                    return

                voting_threshold = self._calculate_voting_threshold(irc, msg, active_users)
                voting = Voting(channel, target, msg.nick, voting_threshold)
                voting.add_vote(msg.nick)

                self.running_votes[voting_id] = voting

                def clean_up():
                    if voting_id in self.running_votes:
                        message = "Abstimmung gegen %s ist erfolglos ausgelaufen." % self.running_votes[voting_id].target
                        irc.queueMsg(ircmsgs.notice(channel, message))
                        del self.running_votes[voting_id]

                schedule.addEvent(clean_up, time.time() + int(self.registryValue("voting_timeout")))

                irc.reply("Abstimmung gegen %s gestartet. Braucht noch %d weitere Stimme(n)." % (
                    target, voting.threshold - 1))

    def gegen(self, irc, msg, args, target):
        """<target>

        Das Scherbengericht gegen <target> wird eröffnet, bzw. Stimmen gegen <target> gezählt.
        """
        self._gegen(irc, msg, target)

    def _user_left(self, irc, nick, channel=None):
        for voting_id in list(self.running_votes):
            voting = self.running_votes[voting_id]
            if nick == voting.target:
                if (channel and channel == voting.channel) or not channel:
                    irc.queueMsg(ircmsgs.notice(voting.channel, "%s hat den Kanal vor Ende der Abstimmung verlassen." % voting.target))
                    self._remove_kebab(irc, voting.channel, nick)
                    del self.running_votes[voting_id]
            elif nick in voting.votes:
                voting.votes.remove(nick)

    def _nick_change(self, irc, old_nick, new_nick):
        for voting_id in list(self.running_votes):
            voting = self.running_votes[voting_id]
            if old_nick == voting.target:
                voting.target = new_nick
                new_voting_id = self._calculate_id(new_nick, voting.channel)
                self.running_votes[new_voting_id] = self.running_votes.pop(voting_id)
            if old_nick == voting.initiator:
                voting.initiator = new_nick
            if old_nick in voting.votes:
                voting.votes.remove(old_nick)
                voting.votes.append(new_nick)

    def _recently_joined(self, irc, join_id):
        if join_id not in self.recently_joined:
            self.recently_joined.append(join_id)

            def remove_recent_join():
                self.recently_joined.remove(join_id)

            schedule.addEvent(remove_recent_join, time.time() + int(self.registryValue("voting_min_age")))

    def doJoin(self, irc, msg):
        if self._is_voting_enabled(irc, msg):
            join_id = self._calculate_id(msg.nick, msg.args[0])
            self._recently_joined(irc, join_id)

    def doPart(self, irc, msg):
        if self._is_voting_enabled(irc, msg):
            self._user_left(irc, msg.nick, channel=msg.args[0])

    def doKick(self, irc, msg):
        if self._is_voting_enabled(irc, msg):
            self._user_left(irc, msg.nick, channel=msg.args[0])

    def doQuit(self, irc, msg):
        self._user_left(irc, msg.nick)

    def doNick(self, irc, msg):
        self._nick_change(irc, msg.nick, msg.args[0])

    def doPrivmsg(self, irc, msg):
        if ircmsgs.isCtcp(msg) and not ircmsgs.isAction(msg):
            return
        if ircutils.isChannel(msg.args[0]) and self._is_voting_enabled(irc, msg):
            channel = msg.args[0]
            message = ircutils.stripFormatting(msg.args[1])
            match = self.regexp.match(message)
            if match and match.group(1) in irc.state.channels[channel].users:
                self._gegen(irc, msg, match.group(1))

    wahlrecht = wrap(wahlrecht)
    schwellwert = wrap(schwellwert)
    abstimmungen = wrap(abstimmungen)
    gegen = wrap(gegen, ["nickInChannel"])

Class = Scherbengericht
