import interface
import saver
import cards_backend
import math
import numpy as np
import discord
import pickle
import copy
from random import randint

testing = True  # This allows bots to join. Bots don't always appear in message.mentions, but can added by role
# sike, not anymore. You have to make them say !join. Change to Discord API :/

if __name__ == '__main__':
    interface.run()


def small_blind_bet(roundnr):
    n = 20*2.5**roundnr
    oom = int(math.log(n, 10))
    return round(n/10**oom)*10**oom


def partioner(amount, partition):
    # divides money in bins according to all-in bets, essentially like tax brackets
    output = []
    partition = [0] + partition
    intervals = [partition[i+1] - partition[i] for i in range(len(partition)-1)]
    out_of_money = False
    for i in intervals:
        if out_of_money:
            output.append(0)
            continue

        if amount < i:
            out_of_money = True
            output.append(amount)
        else:
            output.append(i)
            amount -= i

    # processing remaining amount of money above highest partition
    if out_of_money:
        output.append(0)
    else:
        output.append(amount)

    return output


def get_dominant_color(pil_img):
    img = pil_img.copy()
    img.convert("RGB")
    img.resize((1, 1), resample=0)
    dominant_color = img.getpixel((0, 0))
    return dominant_color

# -------------------------------------------------------------------------------------------------------------------- #


class GameState:
    def __init__(self, channel, host, players):
        self.id = channel.id
        # self.playing = False
        # self.joining = False    # TODO remove?
        self.players = players  # [Player(p,0) for p in players]  # Player attributes may be changed, but not this list
        self.buyin = 1000
        self.host_id = host.id
        self.roundstate = None
        self.roundnumber = 0
        self.sm_blind_index = 0
        self.in_round = False
        self.save()

    def save(self):  # issue: can't pickle channel object, fixed?
        with open("./files/savefile.dat", 'rb') as save_file:
            save_dict = pickle.load(save_file)

        save_dict[self.id] = self

        with open("./files/savefile.dat", 'wb') as save_file:
            pickle.dump(save_dict, save_file)

    def delete(self):
        with open("./files/savefile.dat", 'rb') as save_file:
            save_dict = pickle.load(save_file)

        del save_dict[self.id]

        with open("./files/savefile.dat", 'wb') as save_file:
            pickle.dump(save_dict, save_file)

    @property
    def n_current_players(self):
        # return sum(1 for p in self.players if not p.eliminated)
        print("current players:", [p.name for p in self.current_players])
        return len(self.current_players)

    @property
    def current_players(self):
        return [p for p in self.players if not p.eliminated]

    # def new_round(self):
    #     self.roundstate = RoundState()

    # bedenk welke nuttig is ^ \/
    def end_round(self):
        self.roundstate = None
        for p in self.players:
            p.prstate = None


def channel_occupied(channel):
    save_file = open("./files/savefile.dat", 'rb')
    save_dict = pickle.load(save_file)
    save_file.close()
    return channel.id in save_dict


def bot(user):
    return user.bot and not testing


def user_set_from_message(msg):
    # obtains the set of users that are tagged in a message, by person, role, @here or @everyone
    channel = msg.channel
    cmd, *args = [s.lower() for s in msg.content[1:].split()]
    if '@everyone' in args:
        user_set = {u for u in channel.members if not bot(u)}  # msg.channel.members

    else:
        user_set = set()
        if '@here' in args:
            people = [member for member in channel.members if str(member.status) == "online"]
            user_set = user_set.union({u for u in people if not bot(u)})

        for role in msg.role_mentions:
            print("role members:")
            nameprint(role.members)
            role_members_in_channel = set(role.members).intersection(set(channel.members))
            print("of which in channel")
            nameprint(role_members_in_channel)
            user_set = user_set.union(role_members_in_channel)

        user_set = user_set.union(set(msg.mentions))

    return user_set

# -------------------------------------------------------------------------------------------------------------------- #


class PRState:          # PlayerRoundState: stores all round-specific player data
    def __init__(self, hole_cards):
        self.hole_cards = cards_backend.CardSet(hole_cards)
        self.hand = None
        self.folded = False
        self.all_in = False     # p
        self.invested = 0


class Player:
    def __init__(self, player_obj, init_money: int):
        self.name = player_obj.name
        self.money = init_money
        self.id = player_obj.id
        self.prstate = None
        self.eliminated = False
        self.current_bet = 0

    def __eq__(self, other):
        if not hasattr(other, 'id'):
            return False

        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    async def send(self, *args, **kwargs):
        player_obj = interface.client.get_user(self.id)
        if player_obj.bot:
            print("Tried to send message to bot")
            return
        await player_obj.send(*args, **kwargs)

    def __repr__(self):
        return f"{self.name}.obj"

    def mention(self):
        player_obj = interface.client.get_user(self.id)
        return player_obj.mention

    def object(self):
        return interface.client.get_user(self.id)

    def new_round(self):    # deletes round-specific data
        self.prstate = None

    async def dm_hole_cards(self):
        await self.prstate.hole_cards.send_to(self)

    async def display_hand(self, channel, show_name=True):
        hand = self.prstate.hand
        cards = cards_backend.CardSet(hand.handvalue[1:])
        if show_name:
            await channel.send("{}: {}".format(self.name, hand.handname))
        else:
            await channel.send(hand.handname)
        await cards.send_to(channel)


def nameprint(player_list):
    print([p.name for p in player_list])


async def lobby(host, channel, settings):     # joining process and starting game
    description = "The host can add players by typing \"!add\" and tagging members in" \
                  " the same message, or you can join yourself with \"!join\" if this is enabled. " \
                  "A maximum of 23 players can join."
    lobby_message_embed = discord.Embed(title="The lobby is now open!", description=description,
                                        colour=discord.Colour.green())
    await channel.send(embed=lobby_message_embed)

    user_set = {host}
    user_list = []

    def check(msg):
        cmd = msg.content.split()[0]
        return cmd in ['!join', '!start', '!add', '!remove', '!leave', '!botadd']

    while True:
        print("User set:")
        nameprint(user_set)
        msg = await interface.wait_for_msg(channel, check=check)
        cmd, *args = [s.lower() for s in msg.content[1:].split()]
        if cmd == 'join':
            if settings['joining_mode'] == 3 and not moderator(msg.author, settings):
                continue
            if bot(msg.author):
                continue
            user_set.add(msg.author)
            await channel.send("Welcome, {}".format(msg.author.name))

        elif cmd == 'add':
            if not moderator(msg.author, settings):
                continue

            add_set = user_set_from_message(msg)
            if len(add_set) == 0:
                await channel.send("To use !add, tag at least one person, role, @ here or @ everyone"
                                   " in the same message")
            else:
                user_set = user_set.union(add_set)
                await channel.send("Welcome {}".format(', '.join([member.name for member in add_set])))

        elif cmd == 'remove':
            if not moderator(msg.author, settings):
                continue

            remove_set = user_set_from_message(msg)
            user_set = user_set.difference(remove_set)
            if len(remove_set) == 0:
                await channel.send("To use !remove, tag at least one person, role, @ here or @ everyone"
                                   " in the same message")
            else:
                await channel.send("Removed {}".format(', '.join([member.name for member in remove_set])))

        elif cmd == 'leave':
            user_set.discard(msg.author)
            await channel.send("{} left".format(msg.author.name))

        elif cmd == 'start':
            user_set.add(host)  # just in case
            # if testing:
            #     user_list = list(user_set)  # bot unsafe, for testing
            # else:
            #     user_list = [u for u in user_set if not u.bot]
            user_list = [u for u in user_set if not bot(u)]

            if msg.author == host:

                if len(user_list) > 23:
                    await channel.send('There are currently {} players while the maximum is 23. \n'
                                       'Please remove at least {} players'.format(len(user_set), len(user_set)-23))
                elif len(user_list) < 2:
                    await channel.send("You need at least 2 players to start the game")
                else:
                    await channel.send('Joining has closed. \n'
                                       'Starting the game with {}'.format(', '.join([m.name for m in user_set])))
                    break

        elif cmd == 'botadd':
            add_set = {u for u in channel.members if u.name != 'PokerBot'}
            user_set = user_set.union(add_set)
            await channel.send("Welcome {}".format(', '.join([member.name for member in add_set])))

    return user_list


async def new_game(host, channel):
    yesnodict = {"✅": 'yes', "❌": 'no'}
    print('channel id:', channel.id)
    if channel_occupied(channel):
        channel_occ_embed = discord.Embed(title="There is an active game in this channel.",
                                          description="Do you want to end it and start a new one?",
                                          color=discord.Colour.red())
        #channel_occ_embed.add_field(name="Yes        No", value="[✅]--[❌]")
        # channel_occ_embed.add_field(name="no", value=)
        sent_message = await channel.send(embed=channel_occ_embed)
        # await channel.send("There is an active game in this channel. Do you want to end it and start a new one?")

        # response = await interface.reaction_menu(yesnodict, host, channel)
        response = await interface.reaction_menu_replyv(yesnodict, host, sent_message)
        if response == 'no':
            return

    change_settings_embed = discord.Embed(title="Do you want to change the settings?", color=discord.Colour.blue())
    #change_settings_embed.add_field(name="Yes        No", value="[✅]--[❌]")
    sent_message = await channel.send(embed=change_settings_embed)
    response = await interface.reaction_menu_replyv(yesnodict, host, sent_message)
    default_settings = {'mod_mode': 'host', 'joining_mode': 2}
    if response == 'yes':
        settings = await settings_menu(host, channel, default_settings)
        await channel.send("Settings saved")
    else:
        settings = default_settings
    userlist = await lobby(host, channel, settings)
    # initlist = [900, 800, 1000]
    playerlist = [Player(p, 1000) for p in userlist]
    # playerlist = []
    # for i, p in enumerate(userlist):
    #     playerlist.append(Player(p, initlist[i]))
    gamestate = GameState(channel, host, playerlist)  # saver.GameState(channel, host, playerlist)
    gamestate.save()
    await game(channel)


# place somewhere else probably
def moderator(user, settings):
    mods = settings['moderators']
    if mods is None:
        return True
    else:
        return user in mods


def set_moderators(x):
    pass


async def settings_menu(host, channel, default_settings):
    settings = copy.copy(default_settings)
    settings_trackers = []

    # Choose moderators
    mod_embed = discord.Embed(title="Change who has moderator rights",
                              description="Default is just the host. For the tagging option, tag users in a message "
                                          "starting with !mod before you save the settings.")

    mod_embed.add_field(name="Host", value="🤵")
    mod_embed.add_field(name="Tag people", value="🏷️")
    mod_embed.add_field(name="Everybody", value="👨‍👩‍👦‍👦")

    mod_message = await channel.send(embed=mod_embed)
    options_dict = {'🤵': 'host', '🏷️': 'tag', '👨‍👩‍👦‍👦': 'all'}
    settings_trackers.append(await interface.button_tracker_menu(options_dict, mod_message, host, name='mod_mode'))

    # choice = await interface.reaction_menu_replyv({'🤵': 'host', '🏷️': 'tag', '👨‍👩‍👦‍👦': 'all'}, host, mod_message)
    # if choice == 'tag':
    #     await channel.send("Tag the people you want to make mods in a message starting with !mod")
    #     reply = await interface.wait_for_msg(channel, lambda m: m.content[:4] == 'mod' and m.author == host)
    #     settings['moderators'] = list(user_set_from_message(reply).union(set(host)))
    # elif choice == 'all':
    #     settings['moderators'] = None

    # Choose joining mode
    joining_mode_embed = discord.Embed(title="Choose joining mode",
                                       description="Channel members can join \n"
                                                   "1. Freely at all times\n"
                                                   "2. Freely before the game starts,"
                                                   " during the game only added by mods\n"
                                                   "3. Only mods can add players")  # default 2
    join_message = await channel.send(embed=joining_mode_embed)
    # choice = await interface.reaction_menu_replyv({'1️⃣': 1, '2️': 2, '️️3️': 3}, host, join_message)
    options_dict = {'1️⃣': 1, '2️⃣': 2, '3️⃣': 3}
    settings_trackers.append(await interface.button_tracker_menu(options_dict, join_message, host, name='joining_mode'))

    multiple_votes_list = []

    saving_embed = discord.Embed(title="Save your choices", color=discord.Colour.blue(),
                                 description="Choose 💾 to save your choices, or ↩️to reset choices to default")
    saving_message = await channel.send(embed=saving_embed)
    saving_tracker = await interface.button_tracker_menu({'💾': "save", '↩': "default"}, saving_message, host)
    while True:
        choice = await saving_tracker.wait_for_reaction()
        await saving_tracker.clear_votes()
        if choice == "default":
            return default_settings

        for tracker in settings_trackers:
            choice = await tracker.read()
            if choice is None:
                multiple_votes_list.append(tracker.name)
            else:
                settings[tracker.name] = choice
        if len(multiple_votes_list) == 0:
            break
        else:
            await channel.send("You selected multiple options for the following settings: {} \n"
                         "Please select only 1 option for each and save.".format(', '.join(multiple_votes_list)))
            multiple_votes_list = []

    if settings['mod_mode'] == 'tag':
        pass  # to be implemented
    elif settings['mod_mode'] == 'host':
        settings['moderators'] = [host]
    elif settings['mod_mode'] == 'all':
        settings['moderators'] = None

    return settings


#############

async def jumpstart(channel):
    await game(channel)


async def game(channel):
    while True:
        gamestate = saver.get_gamestate(channel)
        if gamestate.n_current_players == 1:
            break
        await poker_round(channel)

    await channel.send("{} won the game!".format(gamestate.current_players[0].name))
    gamestate.delete()


async def single_round_game(host, channel):
    settings = settings_menu(host, channel)
    userlist = await lobby(host, channel, settings)
    playerlist = [Player(p, 1000) for p in userlist]
    gamestate = saver.GameState(channel, host, playerlist)
    gamestate.save()
    await poker_round(channel)


async def poker_round(channel):
    print('round started')

    gamestate = saver.get_gamestate(channel)
    gamestate.roundnumber += 1
    current_players = gamestate.current_players
    print("In round?", gamestate.in_round)
    if not gamestate.in_round:
        gamestate.in_round = True
        deck = cards_backend.shuffled_deck()
        for p in current_players:
            hole_cards, deck = deck[:2], deck[2:]
            p.prstate = PRState(hole_cards)
            await p.dm_hole_cards()

        print('cards dm\'d')
        community_cards = deck[:5]
        sm_blind_index = gamestate.sm_blind_index
        gamestate.roundstate = saver.RoundState(current_players, sm_blind_index, community_cards)
        gamestate.save()

    roundstate = gamestate.roundstate
    # await roundstate.send_community_cards(channel)

    print("Previous raiser is {}".format(roundstate.previous_raiser))
    print("Turn player is {}".format(roundstate.turn_player))

    while True:
        if roundstate.n_active_players() == 0:  # round ends if everybody is all-in or folded
            break

        if len(roundstate.non_folded_players()) == 1:  # all but 1 folded => win by default
            break

        if roundstate.new_cycle_flag:
            if roundstate.n_active_players() == 1:
                break
            roundstate.cycle_number += 1
            if roundstate.cycle_number <= 3:
                n = [0, 3, 4, 5][roundstate.cycle_number - 1]
                roundstate.reveal_cards(n)
                caption = ''
                if n == 0:
                    if gamestate.roundnumber == 1:
                        caption = "*Let\'s begin!*"
                    else:
                        caption = "*New round!*"
                elif n == 3:
                    caption = "Flop!"
                elif n == 4:
                    caption = "Turn!"
                elif n == 5:
                    caption = "River!"
                await roundstate.send_community_cards(channel, caption=caption)
                roundstate.new_cycle_flag = False

            # print('previous raiser:', roundstate.previous_raiser.name)
            else:   # betting is finalized, on to pot splitting
                break

        if roundstate.turn_number == 1:
            roundstate = await blind_turn(roundstate, channel, 1, gamestate.roundnumber)

        elif roundstate.turn_number == 2:
            roundstate = await blind_turn(roundstate, channel, 2, gamestate.roundnumber)

        else:
            if roundstate.turn_number == 3:  # exception for small and big blind: allowed to raise on blinds edit
                roundstate.previous_raiser = roundstate.turn_player
            roundstate = await turn(roundstate, channel)  # execute turn and update roundstate

        roundstate.next_player()
        # if roundstate.new_cycle_flag:

        gamestate.roundstate = roundstate
        gamestate.save()

    await channel.send("These were the community cards:")
    roundstate.reveal_cards(5)
    await roundstate.send_community_cards(channel)

    for p in roundstate.current_players:   # adds entire hand to each player to determine winner(s) (folded may also
        # want to show hand)
        p.prstate.hand = (roundstate.community_cards + p.prstate.hole_cards).to_hand()

    sorted_playerlist = sorted(roundstate.non_folded_players(), key=lambda x: x.prstate.hand, reverse=True)

    winners = [sorted_playerlist[0]]
    for p in sorted_playerlist[1:]:  # adds all draw winners to winners list
        if p.prstate.hand == sorted_playerlist[0].prstate.hand:
            winners.append(p)
        else:
            break

    if len(roundstate.non_folded_players()) == 1:
        winner = winners[0]   # 1 non-folded => 1 winner
        await channel.send("{} won! \nSince everybody else folded, you may choose not to show your hand."
                           " Do you want to show your hand?".format(winners[0].name))
        yesnodict = {"✅": 'yes', "❌": 'no'}
        response = await interface.reaction_menu(yesnodict, winner, channel)
        if response == 'yes':
            await winner.display_hand(channel)

    else:
        await channel.send("{} won!\nThis was the winning hand:".format(' & '.join([w.mention() for w in winners])))
        await winners[0].display_hand(channel, show_name=False)

    competing_losers = [m for m in roundstate.non_folded_players() if m not in winners]
    competing_losers.sort(key=lambda x: x.prstate.hand)
    if len(competing_losers) != 0:
        await channel.send("These are the hands of the losing players who didn't fold:")
    for cl in competing_losers:
        await cl.display_hand(channel)

    folded = roundstate.folded_players()
    if len(folded) != 0:
        timeout = 15
        yeslist = await interface.button("Players who folded can show their hand by pressing the button below"
                                         " this message in the next {} seconds".format(timeout), folded, channel,
                                         timeout)
        for p in yeslist:
            await p.display_hand(channel)

    # POT DIVISION #
    roundstate.sidepots = sorted(list(set(roundstate.sidepots)))
    pot = np.zeros(len(roundstate.sidepots) + 1, dtype=int)
    for p in roundstate.current_players:    # calculates amounts in sidepots
        partition = partioner(p.prstate.invested, roundstate.sidepots)
        pot += partition
        p.prstate.partition = partition

    for w in winners:   # determines which players are eligible for which amount of money
        w.prstate.winnings = 0  # not relevant to this loop, needed later
        eligibility = 0    # eligibility is the max index of pot a player may take from
        # (0 means eligible for lowest sidepot)
        for amount in roundstate.sidepots:
            if w.prstate.invested > amount:
                eligibility += 1
        w.prstate.eligibility = eligibility

    max_eligibility = 0  # if max_eligiblity < len(pot)-1, some losers will have money returned from pot
    for pot_index, amount in enumerate(pot):
        eligible = [w for w in winners if w.prstate.eligibility >= pot_index]
        for w in eligible:
            w.prstate.winnings += int(pot[pot_index]/len(eligible))
        if (not len(eligible) == 0) and pot_index > max_eligibility:  # update max eligibility
            max_eligibility = pot_index

    for w in winners:
        w.money += w.prstate.winnings
        await channel.send("{} won ${}".format(w.name, w.prstate.winnings - w.prstate.invested))  # TODO: net gain or total winnings?

    if max_eligibility != len(pot) - 1:  # if this is the case, all money has been divided so no returns
        for p in current_players:
            returns = sum(p.prstate.partition[max_eligibility+1:])
            if returns > 0:
                p.money += returns
                await channel.send("${} was returned to {}".format(returns, p.name))
    #############

    eliminated = []
    for p in current_players:
        if p.money == 0:
            p.eliminated = True
            eliminated.append(p)
    elimination_text = ', '.join([p.mention() for p in eliminated])
    if len(eliminated) == 0:
        await channel.send("Everybody survived this round")
    elif len(eliminated) == 1:
        await channel.send(elimination_text + ' was eliminated this round')
    elif len(eliminated) > 1:
        await channel.send(elimination_text + ' were eliminated this round')



    gamestate.sm_blind_index = (gamestate.sm_blind_index + 1) % gamestate.n_current_players   # advance sm_blind_index, clear player roundstates
    gamestate.in_round = False
    print('end of round check^', gamestate.n_current_players)

    gamestate.save()

    print('round ended')
    # initiate new roundstate, TODO: including community cards
    # initiate new prstate for each player, including hole cards
    # while previous raiser !=


async def turn(roundstate, channel):
    print(', '.join(["{}: ${}".format(p.name, p.money) for p in roundstate.current_players]))
    player = roundstate.turn_player

    # select available moves for current player
    current_options = ['fold', 'all-in']
    if roundstate.n_active_players() == 1:  # if one player left who has yet to equal the bet
        if player.prstate.invested < roundstate.min_bet:
            if player.money > roundstate.min_bet - player.prstate.invested:
                current_options.append('call')
                current_options.remove('all-in')  # all-in would constitute pointless raise

            # if not, default options apply

        else:
            return roundstate
    else:
        if player.prstate.invested == roundstate.min_bet:
            current_options.append('check')
            current_options.remove('fold')
        elif player.money + player.prstate.invested > roundstate.min_bet:  # strict inequality, equality is all-in
            current_options.append('call')
        if player.money >= roundstate.min_bet - player.prstate.invested + roundstate.min_raise:  # TODO: changed...
            current_options.append('raise')

    # show players game status
    status_embed = discord.Embed(title="Game status")
    # if roundstate.public_cards[0].suit != 0:  # everything covered
    #     status_embed.add_field(name="Cards",
    #                            value=' - '.join([card.emojiprint() for card in roundstate.public_cards]))

    await roundstate.public_cards.save_image_to("./files/CardImgBuffer.png")
    cc_image = discord.File("./files/CardImgBuffer.png", "communitycards.png")
    status_embed.set_thumbnail(url="attachment://communitycards.png")

    folded = roundstate.folded_players()
    if len(folded) != 0:
        status_embed.add_field(name="Folded", value=', '.join([p.name for p in folded]), inline=False)

    all_in_p = roundstate.all_in_players()
    if len(all_in_p) != 0:
        status_embed.add_field(name="All-in",
                               value=', '.join([p.name + " (at $" + str(p.prstate.invested) + ")" for p in all_in_p]),
                               inline=False)

    active_p = roundstate.active_players()
    if len(active_p) != 0:
        status_embed.add_field(name="Active players",
                               value=', '.join([p.name + " ($" + str(p.money) + ")" for p in active_p]), inline=False)

    status_embed.add_field(name="Bet", value='$'+str(roundstate.min_bet))
    status_embed.add_field(name="Minimum raise", value='$'+str(roundstate.min_raise))
    status_embed.add_field(name="Pot", value='$'+str(roundstate.pot_amount()))
    # status_embed.set_footer(text=player.name, icon_url=player.object().avatar_url)

    await channel.send(file=cc_image, embed=status_embed)  # send status update in chat

    print('initiated {}\'s turn'.format(player.name))

    # try:  # try to make the embed a representative color for the player
    #     player_avatar = await (player.object().avatar_url_as(format='png')).read()
    #     avatar_dominant_color = discord.Colour(*get_dominant_color(player_avatar))
    # except ValueError:
    #     avatar_dominant_color = discord.Colour.purple()
    turn_embed = discord.Embed(title="{}, it's your turn".format(player.name),
                               colour=discord.Colour.gold())
    turn_embed.add_field(name="Money", value='$'+str(player.money))
    turn_embed.add_field(name="Your bet", value='$'+str(player.prstate.invested))
    total_options_dict = {"✅": 'check', "🛑": 'fold', "💯": 'all-in', "🆙": 'raise', "☎️": 'call'}
    inv = {y: x for x, y in total_options_dict.items()}
    current_option_dict = {inv[s]: s for s in current_options}  # selects applicable options

    formatted_move_name_dict = {'check': 'Check ', 'fold': 'Fold   ',
                                'all-in': 'All-in', 'call': '   Call   ', 'raise': 'Raise'}
    formatted_emoji_dict = {'check': "[✅]", 'fold': "[🛑]", 'all-in': "[💯]", 'call': "[☎]", 'raise': "[🆙]"}

    your_move_names = []
    your_emojis = []
    for emoji, move_name in current_option_dict.items():
        your_move_names.append(formatted_move_name_dict[move_name])
        your_emojis.append(formatted_emoji_dict[move_name])
        # turn_embed.add_field(name=move_name, value=emoji)

    turn_embed.add_field(name='   '.join(your_move_names), value=' -- '.join(your_emojis), inline=False)

    sent_message = await channel.send(player.mention(), embed=turn_embed)

    move = await interface.reaction_menu_replyv(current_option_dict, player, sent_message)
    if move == 'check':
        post_turn_embed = discord.Embed(title="{} checked".format(player.name), colour=discord.Colour.green())

    elif move == 'call':
        post_turn_embed = discord.Embed(title="{} called".format(player.name, roundstate.min_bet),
                                        colour=discord.Colour.teal())
        player.money -= roundstate.min_bet - player.prstate.invested  # edit
        player.prstate.invested = roundstate.min_bet  # edit

    elif move == 'fold':
        post_turn_embed = discord.Embed(title="{} folded".format(player.name),
                                        colour=discord.Colour.red())
        player.prstate.folded = True

    elif move == 'raise':
        await channel.send("How much do you want to raise? (Send \'cancel\' to cancel)")

        def check(msg):
            if msg.author.id != player.id:
                return False
            return msg.content.isdigit() or msg.content == 'cancel'

        while True:
            raise_response = (await interface.wait_for_msg(channel, check)).content
            if raise_response == 'cancel':  # redo turn
                roundstate = await turn(roundstate, channel)
                return roundstate

            raise_amount = int(raise_response)
            if roundstate.min_bet + raise_amount > player.money:
                await channel.send("You don't have enough money to raise that amount.")

            elif roundstate.min_raise > raise_amount:
                await channel.send("The minimum raise is ${}".format(roundstate.min_raise))
            else:
                if raise_amount == 69:
                    await channel.send(file=discord.File('./files/nice_gif.gif'))

                if raise_amount == 420:
                    await channel.send("Blaze it")
                break

        player.money -= roundstate.min_bet - player.prstate.invested + raise_amount
        player.prstate.invested = roundstate.min_bet + raise_amount
        roundstate.min_bet += raise_amount
        roundstate.min_raise = raise_amount
        roundstate.previous_raiser = player

        if player.money == 0:  # if player raises all his money, he goes all-in
            player.prstate.all_in = True
            roundstate.sidepots.append(player.prstate.invested)
            post_turn_embed = discord.Embed(title="{} went all-in".format(player.name),
                                            colour=discord.Colour.orange())
        else:
            post_turn_embed = discord.Embed(title="{} raised ${}".format(player.name, raise_amount),
                                            colour=discord.Colour.blue())

    elif move == 'all-in':
        await channel.send("Are you sure you want to go all-in?")
        yesnodict = {"✅": 'yes', "❌": 'no'}
        answer = await interface.reaction_menu(yesnodict, player, channel)
        if answer == 'no':      # redo turn
            roundstate = await turn(roundstate, channel)
            return roundstate

        bet = player.money
        raise_amount = bet - roundstate.min_bet
        player.prstate.invested += bet
        player.money = 0
        player.prstate.all_in = True
        roundstate.sidepots.append(player.prstate.invested)

        # two cases: all in is a raise (player.money > min_bet) or is a check (player.money <= min_bet)

        if bet > roundstate.min_bet:  # all-in behaves like raise
            print("all-in went to raise")
            roundstate.previous_raiser = player
            roundstate.min_bet = player.prstate.invested  # += bet   # TODO is dit zo? += of = ... changed
            if raise_amount > roundstate.min_raise:  # if all-in raise exceeds minimum raise, minimum raise is adjusted
                roundstate.min_raise = raise_amount

        else:  # like check
            pass  # nothing special happens, really. Might later though.

        post_turn_embed = discord.Embed(title="{} went all-in".format(player.name),
                                        colour=discord.Colour.orange())

    else:
        print("Move not registered")
        post_turn_embed = discord.Embed(title="Move not registered.")

    print(player is roundstate.turn_player)
    if move != 'all-in' and move != 'fold':
        post_turn_embed.description = "You have ${} left".format(player.money)

    await channel.send(embed=post_turn_embed)

    total_money = sum(p.money for p in roundstate.current_players) + \
                  sum(p.prstate.invested for p in roundstate.current_players)

    print('total money:  $', total_money)
    return roundstate


async def blind_turn(roundstate, channel, size, roundnumber):  # TODO: check rules. For now, bet or all-in automatically
    # size: 1=small, 2 = big
    player = roundstate.turn_player

    title = "{}, you're the {} blind ".format(player.name, ['small', 'big'][size-1])

    blind_amounts = [0, 10 + 5*roundnumber, 20 + 10*roundnumber]  # hardcoded for now, TODO: changeable in settings
    amount = blind_amounts[size]
    roundstate.min_bet = amount
    roundstate.min_raise = blind_amounts[size] - blind_amounts[size-1]
    if amount >= player.money:
        player.prstate.invested = player.money
        player.money = 0
        player.prstate.all_in = True
        roundstate.sidepots.append(player.prstate.invested)
        description = "You were forced to go all-in.\n" \
                      "The current bet is ${} and the minimum raise is ${}".format(roundstate.min_bet,
                                                                                   roundstate.min_raise)

    else:
        player.money -= amount
        print("Blind money processing check")
        print("    ", player.money, roundstate.turn_player.money)
        print(player.money)
        player.prstate.invested = amount

        description = "You bet ${} . You have ${} left.".format(amount, player.money)
    blind_embed = discord.Embed(title=title, description=description, colour=discord.Colour.lighter_grey())
    await channel.send(player.mention(), embed=blind_embed)
    return roundstate
