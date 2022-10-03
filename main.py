import json
import os

import discord
from discord import Member, Message, Role
from discord.ext import commands

import database


_PREFIX = ";;"
_USER_ROLE_NAMES = ("user",)  # all lowercase
_ADMIN_ROLE_NAMES = ("admin",)  # all lowercase
_MODERATOR_ROLE_NAMES = ("moderator",)  # all lowercase
_PERMISSION_LEVEL_MAPPING = {1: "käyttäjä", 2: "moderaattori", 3: "admin"}


default_permissions = discord.Permissions(
    add_reactions=True,
    connect=True,
    embed_links=True,
    external_emojis=True,
    request_to_speak=True,
    send_messages=True,
    speak=True,
    use_external_emojis=True,
    use_slash_commands=True,
    use_voice_activation=True,
    view_channel=True,
    stream=True
)


with open("phrases.json", "r", encoding="utf-8") as phrase_file:
    phrases = json.load(phrase_file)


def create_bot():
    description = phrases["bot_description"]

    intents = discord.Intents.default()
    intents.members = True
    return commands.Bot(command_prefix=_PREFIX, description=description, intents=intents)


bot = create_bot()


if "settings.json" not in os.listdir("persistence"):
    with open("persistence/settings.json", "w", encoding="utf-8") as new_settings:
        new_settings.writelines(("{", "}"))

with open("persistence/settings.json", "r", encoding="utf-8") as settings_file:
    settings = json.load(settings_file)


@bot.event
async def on_message(message: Message):
    if message.author.bot:
        return
    await bot.process_commands(message)
    if message.guild is None and not message.content.startswith(_PREFIX):
        await process_dm(message)


async def process_dm(message: Message):
    user = database.get_user(message.author.id)
    if not user:
        return
    message_content = None
    if not user.nick:
        user.nick = message.content
        org_query = phrases['org_query']
        org_names = "\n".join(database.get_org_names())
        message_content = f"{org_query}\n{org_names}"
    elif not user.orgs:
        org = database.get_org(message.content)
        if not org:
            await message.author.send(phrases["no_org"].format(message.content))
            org_query = phrases['org_query']
            org_names = "\n".join(database.get_org_names())
            await message.author.send(f"{org_query}\n{org_names}")
            return
        user.orgs.append(database.OrgPermissions(org))
        admin_channel = bot.get_channel(settings["admin_channel_id"])
        register_info = phrases["pending_registration"].format(user.user_id, user.nick, org.org_id)
        approval_instructions = phrases["approval_instructions"].format(_PREFIX, "approve", _PREFIX, "reject")
        await admin_channel.send(f"{register_info} {approval_instructions}")
        guild_name = bot.get_guild(settings['guild_id']).name
        message_content = phrases["awaiting_approval"].format(guild_name, user.nick, org.name)
        message_content += f"\n{phrases['retry_info'].format(_PREFIX, 'retry')}"
    database.update_user(user)
    await message.author.send(message_content)


@bot.event
async def on_member_join(member: Member):
    database.add_user(member.id)
    guild_name = bot.get_guild(settings['guild_id']).name
    await member.send(phrases["name_query"].format(guild_name))


@bot.event
async def on_ready():
    print("Connected")


def is_bot_admin():
    async def predicate(ctx):
        user = database.get_user(ctx.author.id)
        return user and 3 in [user_permissions.permission_level for user_permissions in user.orgs]
    return commands.check(predicate)


def is_bot_moderator():
    async def predicate(ctx):
        user = database.get_user(ctx.author.id)
        return user and 2 in [user_permissions.permission_level for user_permissions in user.orgs]
    return commands.check(predicate)


def is_admin_channel():
    async def predicate(ctx):
        return ctx.channel.id == settings["admin_channel_id"]
    return commands.check(predicate)


def is_correct_guild():
    async def predicate(ctx):
        if ctx.guild.id == settings["guild_id"]:
            return True
        else:
            await ctx.send(phrases["wrong_guild"] + " " +
                           phrases["re_register_guild"].format(_PREFIX, "register guild", _PREFIX, "unregister guild"))
    return commands.check(predicate)


@bot.command(aliases=["org", "addorg", "add"])
@is_admin_channel()
@commands.check_any(commands.has_guild_permissions(administrator=True), is_bot_admin())
async def add_org(ctx, *, org_name: str):
    if database.org_exists(org_name):
        return await ctx.send(phrases["org_exists"].format(org_name))
    common_category = await try_create_category(ctx, phrases["common"])
    org_category = await try_create_category(ctx, org_name)
    org_role: discord.Role = next((role for role in ctx.guild.roles if role.name == org_name), None)
    if not org_role:
        org_role = await ctx.guild.create_role(name=org_name, permissions=default_permissions)
    await add_role_permissions(org_role, common_category, org_category)
    database.add_org(database.Org(org_role.id, org_name))
    await ctx.send(phrases["org_added"].format(org_role.id))


async def try_create_category(ctx, category_name):
    category = next((category for category in ctx.guild.categories if category.name == category_name), None)
    if not category:
        overwrites = {ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        category = await ctx.guild.create_category(category_name, overwrites=overwrites)
    return category


async def add_role_permissions(org_role: discord.Role, *categories: discord.CategoryChannel):
    for category in categories:
        overwrites = category.overwrites
        overwrites.update({org_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)})
        await category.edit(overwrites=overwrites)


def ensure_author_permissions(author: Member, to_approve: database.User) -> bool:
    if author.guild_permissions.administrator:
        return True
    author_permissions = database.get_user(author.id).orgs
    wanted_org = to_approve.orgs[0].org
    author_org_permissions = next((org for org in author_permissions if org.org.org_id == wanted_org.org_id), None)
    if not author_permissions or author_org_permissions.permission_level < 2:
        return False
    return True


@bot.command()
@is_admin_channel()
@commands.check_any(commands.has_guild_permissions(administrator=True), is_bot_admin(), is_bot_moderator())
async def approve(ctx, member: Member):
    user = await ensure_user_waiting_approval(ctx, member)
    if not user:
        return
    if not ensure_author_permissions(ctx.author, user):
        return await ctx.send(phrases["invalid_approval_permissions"].format(user.user_id, user.orgs[0].org.org_id,
                                                                             ctx.author.id))
    user.orgs[0].permission_level = 1
    database.update_user(user)
    await member.edit(nick=user.nick)
    await member.add_roles(ctx.guild.get_role(user.orgs[0].org.org_id))
    await ctx.send(phrases["user_approved"].format(user.user_id, user.orgs[0].org.org_id))
    await member.send(phrases["approved_dm"].format(ctx.guild.name, user.orgs[0].org.name))


@bot.command()
@is_admin_channel()
@commands.check_any(commands.has_guild_permissions(administrator=True), is_bot_admin(), is_bot_moderator())
async def reject(ctx, member: Member):
    user = await ensure_user_waiting_approval(ctx, member)
    if not user:
        return
    if not ensure_author_permissions(ctx.author, user):
        return await ctx.send(phrases["invalid_reject_permissions"].format(user.user_id, user.orgs[0].org.org_id,
                                                                           ctx.author.id))
    database.delete_user(user)
    await ctx.send(phrases["user_rejected"].format(user.user_id, user.orgs[0].org.org_id))
    await member.send(phrases["rejected_dm"].format(ctx.guild.name, user.orgs[0].org.name))


async def ensure_user_waiting_approval(ctx, member: Member) -> database.User:
    user = database.get_user(member.id)
    if not user:
        await ctx.send(phrases["no_user_in_db"].format(member.id))
    elif not user.orgs:
        await ctx.send(phrases["need_org_to_approve"])
    elif user.orgs[0].permission_level > 0:
        await ctx.send(phrases["already_registered"].format(member.id, user.orgs[0].org.org_id))
    else:
        return user


@bot.command()
async def retry(ctx):
    if ctx.message.guild:
        return await ctx.send(phrases["dm_only"])
    user = database.get_user(ctx.author.id)
    if user:
        database.delete_user(user)
    database.add_user(ctx.author.id)
    guild_name = bot.get_guild(settings['guild_id']).name
    await ctx.author.send(phrases["name_query"].format(guild_name))


@bot.command()
async def join(ctx, member: Member, org: Role):
    user = database.get_user(member.id)
    if not user:
        return await ctx.send(phrases["no_user_in_db"].format(member.id))
    elif org.id in [user_org.org.org_id for user_org in user.orgs]:
        return await ctx.send(phrases["already_registered"].format(member.id, org.id))
    user_org = database.OrgPermissions(database.get_org(org.id), 1)
    user.orgs.append(user_org)
    database.update_user(user)
    await member.add_roles(ctx.guild.get_role(org.id))
    await ctx.send(phrases["org_joined"].format(user.user_id, user_org.org.org_id))


@bot.command()
async def leave(ctx, member: Member, org: Role):
    user = database.get_user(member.id)
    if not user:
        return await ctx.send(phrases["no_user_in_db"].format(member.id))
    deleted_org = next((user_org for user_org in user.orgs), None)
    if not deleted_org:
        return await ctx.send(phrases["not_org_member"].format(member.id, org.id))
    database.delete_user_org(deleted_org, user.user_id)
    await member.remove_roles(org)
    await ctx.send(phrases["org_left"].format(user.user_id, org.id))


@bot.group()
@commands.has_guild_permissions(administrator=True)
async def register(ctx):
    if not ctx.invoked_subcommand:
        await ctx.send(phrases["invalid_subcommand"].format(_PREFIX, "register", _PREFIX, "register"))


@register.command(name="guild", aliases=["server"])
async def _guild(ctx):
    if "guild_id" in settings:
        await ctx.send(phrases["another_guild_registered"].format(settings["guild_id"]) + " " +
                       phrases["re_register_guild"].format(_PREFIX, "register guild", _PREFIX, "unregister guild"))
    else:
        settings["guild_id"] = ctx.guild.id
        with open("persistence/settings.json", "w", encoding="utf-8") as settings_out:
            json.dump(settings, settings_out)
        await ctx.send(phrases["guild_registered"])


@register.command(name="admin", aliases=["channel"])
@is_correct_guild()
async def _admin(ctx):
    settings["admin_channel_id"] = ctx.channel.id
    with open("persistence/settings.json", "w", encoding="utf-8") as settings_out:
        json.dump(settings, settings_out)
    await ctx.send(phrases["admin_channel_registered"].format(ctx.channel.id))


@bot.group()
@commands.has_guild_permissions(administrator=True)
async def unregister(ctx):
    if not ctx.invoked_subcommand:
        await ctx.send(phrases["invalid_subcommand"].format(_PREFIX, "unregister", _PREFIX, "unregister"))


@unregister.command(name="guild", alias=["server"])
async def _guild(ctx):
    if "guild_id" not in settings or settings["guild_id"] != ctx.guild.id:
        await ctx.send(phrases["guild_not_registered"])
    else:
        settings.pop("guild_id")
        settings.pop("admin_channel_id")
        with open("persistence/settings.json", "w", encoding="utf-8") as settings_out:
            json.dump(settings, settings_out)
        await ctx.send(phrases["guild_unregistered"])


def permission(argument: str):
    try:
        numerical = int(argument)
        if numerical in (1, 2, 3):
            return numerical
        else:
            raise commands.ConversionError(phrases["invalid_permission"].format(_USER_ROLE_NAMES, _ADMIN_ROLE_NAMES,
                                                                                _MODERATOR_ROLE_NAMES),
                                           argument)
    except ValueError:
        pass

    if argument.lower() in _ADMIN_ROLE_NAMES:
        return 3
    elif argument.lower() in _MODERATOR_ROLE_NAMES:
        return 2
    elif argument.lower() in _USER_ROLE_NAMES:
        return 1
    raise commands.ConversionError(phrases["invalid_permission"].format(_USER_ROLE_NAMES, _ADMIN_ROLE_NAMES,
                                                                        _MODERATOR_ROLE_NAMES),
                                   argument)


async def set_channel_permissions(ctx, member, permission_level):
    admin_channel = ctx.guild.get_channel(settings["admin_channel_id"])
    if permission_level < 2:
        return await admin_channel.set_permissions(member, overwrite=None)
    await admin_channel.set_permissions(member, read_messages=True, send_messages=True)


@bot.group()
@is_admin_channel()
@commands.check_any(commands.has_guild_permissions(administrator=True), is_bot_admin())
async def permissions(ctx, member: Member, org_role: Role, permission_level: permission):
    org = database.get_org(org_role.id)
    if not org:
        return await ctx.send(phrases["role_is_not_org"].format(org_role.name))
    user = database.get_user(member.id)
    if not user:
        return await ctx.send(phrases["no_user_in_db"].format(member.id))
    user_org = next((org_permissions for org_permissions in user.orgs if org_permissions.org.org_id == org.org_id),
                    None)
    if not user_org:
        return await ctx.send(phrases["user_not_org_member"].format(member.id, user_org.org.org_id))
    max_permissions = max(permission_level, *[org_permissions.permission_level for org_permissions in user.orgs])
    await set_channel_permissions(ctx, member, max_permissions)
    user_org.permission_level = permission_level
    database.update_user(user)
    await ctx.send(phrases["permissions_updated"].format(member.id, org.org_id,
                                                         _PERMISSION_LEVEL_MAPPING[permission_level]))


if __name__ == '__main__':
    database.init_databases()
    token = os.getenv("TOKEN", None)
    if token == None:
        with open("token.txt", "r", encoding="utf-8") as token_file:
            token = token_file.readline()
    bot.run(token)
