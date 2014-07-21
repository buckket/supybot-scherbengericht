# -*- coding: utf-8 -*-

# ##
# Copyright (c) 2014, MrLoom
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

import math
import time

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.schedule as schedule
import supybot.ircmsgs as ircmsgs


# TODO: Votes laufen nach X Sekunden ab
# TODO: Schutzmechanismus einbauen. Trollgefahr!


class Voting(object):
    def __init__(self, channel, target, initiator):
        self.channel = channel
        self.target = target
        self.initiator = initiator

        self.time = time.time()
        self.votes = []

    def remaining_time(self, voting_timeout):
        return (self.time + voting_timeout) - time.time()

    def add_vote(self, nick):
        if not nick in self.votes:
            self.votes.append(nick)
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
    """Scherbengericht. Genug gesagt."""

    def __init__(self, irc):
        self.__parent = super(Scherbengericht, self)
        self.__parent.__init__(irc)

        self.running_votes = {}

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
        if self.registryValue("gerichtsbarkeit", channel):
            return True
        else:
            if reply:
                irc.reply("Scherbengericht ist in %s nicht gestattet!" % channel)
            return False

    def _check_privileges(self, irc, msg, reply=False):
        channel = msg.args[0]
        if not irc.isChannel(channel):
            if reply:
                irc.reply("Verhandlungen sind öffentlich zu führen!")
            return False
        elif irc.nick not in irc.state.channels[channel].ops:
            if reply:
                irc.reply("%s braucht op ;_;" % irc.nick)
            return False
        else:
            return True

    def _calculate_voting_id(self, target, channel):
        return "%s@%s" % (target, channel)

    def _calculate_voting_threshold(self, irc, msg):
        voting_min = int(self.registryValue("voting_min"))
        threshold = math.ceil(len(irc.state.channels[msg.args[0]].users) * int(self.registryValue("voting_quota")))
        return threshold if threshold > voting_min else voting_min

    def abstimmungen(self, irc, msg, args):
        """

        Zeigt alle laufenden Abstimmungen
        """

        channel = msg.args[0]
        users = irc.state.channels[channel].users
        voting_threshold = self._calculate_voting_threshold(irc, msg)
        voting_timeout = int(self.registryValue("voting_timeout"))

        votes = []
        for voting_id in self.running_votes:
            voting = self.running_votes[voting_id]
            votes.append("[ Abstimmung gegen %s (%d von %d Stimmen) noch %d Sekunden ]" % (
                voting.target,
                voting.count_votes(users),
                voting_threshold,
                voting.remaining_time(voting_timeout)))
        if votes:
            irc.reply(", ".join(votes))
        else:
            irc.reply("Momentan laufen keine Abstimmungen.")

    def gegen(self, irc, msg, args, target):
        """<target>

        Das Scherbengericht gegen <target> wird eröffnet :3
        """

        if self._is_voting_enabled(irc, msg, reply=True) and self._check_privileges(irc, msg, reply=True):

            channel = msg.args[0]
            users = irc.state.channels[channel].users
            voting_id = self._calculate_voting_id(target, channel)
            voting_threshold = self._calculate_voting_threshold(irc, msg)

            if target == msg.nick or target == irc.nick:
                irc.queueMsg(ircmsgs.kick(channel, msg.nick, "Snibeti snab XDD"))
                self.doKick(irc, msg)
                return

            if voting_id in self.running_votes:
                voting = self.running_votes[voting_id]
                if voting.add_vote(msg.nick):
                    voting_count = voting.count_votes(users)
                    if voting_count >= voting_threshold:
                        if target in irc.state.channels[channel].ops:
                            irc.queueMsg(ircmsgs.privmsg(channel, "Einen Versuch war's wert! :--D"))
                            for nick in voting.votes:
                                self._remove_kebab(irc, channel, nick)
                        else:
                            self._remove_kebab(irc, channel, target)
                        del self.running_votes[voting_id]
                    else:
                        irc.reply("Stimme gegen %s registriert. Weitere Stimmen notwendig: %d" % (
                            target, voting_threshold - voting_count))
                else:
                    voting_count = voting.count_votes(users)
                    irc.reply("Du hast bereits gegen %s gestimmt! Weitere Stimmen notwendig: %d" % (
                        target, voting_threshold - voting_count))

            else:
                voting = Voting(channel, target, msg.nick)
                self.running_votes[voting_id] = voting
                voting.add_vote(msg.nick)

                def clean_up():
                    if voting_id in self.running_votes:
                        message = "Antrag gegen %s ist erfolglos ausgelaufen." % self.running_votes[voting_id].target
                        irc.queueMsg(ircmsgs.privmsg(channel, message))
                        del self.running_votes[voting_id]

                schedule.addEvent(clean_up, time.time() + int(self.registryValue("voting_timeout")))

                irc.reply("Abstimmung gegen %s gestartet. Weitere Stimmen notwendig: %d" % (
                    target, voting_threshold - 1))

    def _user_left(self, irc, voting_id, nick, channel=None):
        voting = self.running_votes[voting_id]
        if nick == voting.target:
            if (channel and channel == voting.channel) or not channel:
                irc.queueMsg(ircmsgs.privmsg(voting.channel, "%s hat den Kanal vor Ende der Abstimmung verlassen." % voting.target))
                self._remove_kebab(irc, voting.channel, nick)
                del self.running_votes[voting_id]
        elif nick in voting.votes:
            del voting.votes[nick]

    def _nick_change(self, irc, voting_id, old_nick, new_nick):
        voting = self.running_votes[voting_id]
        if old_nick == voting.target:
            voting.target = new_nick
            new_voting_id = self._calculate_voting_id(new_nick, voting.channel)
            self.running_votes[new_voting_id] = self.running_votes.pop(voting_id)
        if old_nick == voting.initiator:
            voting.initiator = new_nick
        if old_nick in voting.votes:
            voting.votes.remove(old_nick)
            voting.votes.append(new_nick)

    def doPart(self, irc, msg):
        if self._is_voting_enabled(irc, msg):
            for voting_id in list(self.running_votes):
                self._user_left(irc, voting_id, msg.nick, channel=msg.args[0])

    def doKick(self, irc, msg):
        if self._is_voting_enabled(irc, msg):
            for voting_id in list(self.running_votes):
                self._user_left(irc, voting_id, msg.nick, channel=msg.args[0])

    def doQuit(self, irc, msg):
        for voting_id in list(self.running_votes):
            self._user_left(irc, voting_id, msg.nick)

    def doNick(self, irc, msg):
        for voting_id in list(self.running_votes):
            self._nick_change(irc, voting_id, msg.nick, msg.args[0])

    abstimmungen = wrap(abstimmungen)
    gegen = wrap(gegen, ["nickInChannel"])


Class = Scherbengericht
