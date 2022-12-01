import json
import os
from enum import Enum
from typing import List

import discord
from discord import Member, Role, Client, Intents, Interaction, Guild, AppCommandOptionType
from discord.app_commands import CommandTree, describe, Transformer, Choice, TransformerError
import discord.app_commands

import database


def get_env_or_file(var_name: str) -> str:
    var = os.getenv(var_name, None)
    if var is None:
        with open(f"{var_name}.txt", "r", encoding="utf-8") as var_file:
            var = var_file.readline()
    return var


default_permissions = discord.Permissions(
    add_reactions=True,
    connect=True,
    embed_links=True,
    external_emojis=True,
    request_to_speak=True,
    send_messages=True,
    speak=True,
    use_external_emojis=True,
    use_application_commands=True,
    use_voice_activation=True,
    view_channel=True,
    stream=True
)


_MY_GUILD = discord.Object(id=int(get_env_or_file("GUILD")))


with open("phrases.json", "r", encoding="utf-8") as phrase_file:
    phrases = json.load(phrase_file)


class Bot(Client):
    def __init__(self, description: str, intents: Intents):
        super().__init__(description=description, intents=intents)
        self.tree = CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=_MY_GUILD)
        await self.tree.sync(guild=_MY_GUILD)


def create_bot():
    description = phrases["bot_description"]
    intents = discord.Intents.default()
    intents.members = True
    return Bot(description=description, intents=intents)


bot = create_bot()

if "settings.json" not in os.listdir("persistence"):
    with open("persistence/settings.json", "w", encoding="utf-8") as new_settings:
        new_settings.writelines(("{", "}"))

with open("persistence/settings.json", "r", encoding="utf-8") as settings_file:
    settings = json.load(settings_file)


@bot.event
async def on_member_join(member: Member):
    database.add_user(member.id)


@bot.event
async def on_ready():
    print("Connected")


def is_bot_admin():
    async def predicate(interaction: Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        user = database.get_user(interaction.user.id)
        return 3 in [user_permissions.permission_level for user_permissions in user.orgs]
    return discord.app_commands.check(predicate)


def is_bot_moderator():
    async def predicate(interaction: Interaction):
        user = database.get_user(interaction.user.id)
        return 2 in [user_permissions.permission_level for user_permissions in user.orgs]
    return discord.app_commands.check(predicate)


def is_admin_channel():
    async def predicate(interaction: Interaction):
        return "admin_channel_id" in settings and interaction.channel.id == settings["admin_channel_id"]
    return discord.app_commands.check(predicate)


def is_correct_guild():
    async def predicate(interaction: Interaction):
        if interaction.guild.id == settings["guild_id"]:
            return True
        else:
            message = phrases["wrong_guild"] + " " + \
                      phrases["re_register_guild"].format("/", "register guild", "/", "unregister guild")
            await interaction.response.send_message(message)
    return discord.app_commands.check(predicate)


def user_in_org(org_name: str, user: database.User, min_level: int = 0, max_level: int = 3):
    for org in user.orgs:
        if org.org.name == org_name:
            return min_level <= org.permission_level <= max_level
    return False


class OrganisationBase(Transformer):
    async def transform(self, interaction: Interaction, value: str) -> database.Org:
        org = database.get_org(value)
        if not org:
            await interaction.response.send_message(phrases["no_org"].format(value), ephemeral=True)
            raise Exception()
        org.role = interaction.guild.get_role(org.org_id)
        return org

    async def autocomplete(self, interaction: Interaction, value: str) \
            -> List[Choice[str]]:
        all_org_choices = [Choice(name=org, value=org) for org in database.get_org_names()]
        choices = self._conditional_hook(interaction, all_org_choices)
        return [choice for choice in choices if self._text_matches(value, choice.name)]

    @staticmethod
    def _text_matches(value: str, org_name: str):
        return (value.lower() in org_name.lower()) or not value

    def _conditional_hook(self, interaction: Interaction, orgs: list[Choice[str]]) \
            -> list[Choice[str]]:
        pass


class JoinableOrganisation(OrganisationBase):
    def _conditional_hook(self, interaction: Interaction, orgs: list[Choice[str]]) \
            -> list[Choice[str]]:
        user = database.get_user(interaction.user.id)
        return [org for org in orgs if not user_in_org(org.value, user)]


class AddableOrganisation(OrganisationBase):
    def _conditional_hook(self, interaction: Interaction, orgs: list[Choice[str]]) \
            -> list[Choice[str]]:
        user = database.get_user(interaction.namespace.member.id)
        return [org for org in orgs if not user_in_org(org.value, user)]


class LeavableOrganisation(OrganisationBase):
    def _conditional_hook(self, interaction: Interaction, orgs: list[Choice[str]]) \
            -> list[Choice[str]]:
        user = database.get_user(interaction.user.id)
        return [org for org in orgs if user_in_org(org.value, user, 1)]


class RemovableOrganisation(OrganisationBase):
    def _conditional_hook(self, interaction: Interaction, orgs: list[Choice[str]]) \
            -> list[Choice[str]]:
        user = database.get_user(interaction.namespace.member.id)
        return [org for org in orgs if user_in_org(org.value, user, 1)]


class ApprovableOrganisation(OrganisationBase):
    def _conditional_hook(self, interaction: Interaction, orgs: list[Choice[str]]) \
            -> list[Choice[str]]:
        user = database.get_user(interaction.namespace.member.id)
        return [org for org in orgs if user_in_org(org.value, user, max_level=0)]


@bot.tree.command(description=phrases["add_org"])
@describe(org_name=phrases["org_name"])
@is_bot_admin()
async def add_org(interaction: Interaction, *, org_name: str):
    if database.org_exists(org_name):
        return await interaction.response.send_message(phrases["org_exists"].format(org_name), ephemeral=True)
    common_category = await try_create_category(interaction, phrases["common"])
    org_category = await try_create_category(interaction, org_name)
    org_role: discord.Role = next((role for role in interaction.guild.roles if role.name == org_name), None)
    if not org_role:
        org_role = await interaction.guild.create_role(name=org_name, permissions=default_permissions)
    await add_role_permissions(org_role, common_category, org_category)
    database.add_org(database.Org(org_role.id, org_name))
    await interaction.response.send_message(phrases["org_added"].format(org_role.id), ephemeral=True)


async def try_create_category(interaction: Interaction, category_name: str):
    category = next((category for category in interaction.guild.categories if category.name == category_name), None)
    if not category:
        overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        category = await interaction.guild.create_category(category_name, overwrites=overwrites)
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


@bot.tree.command(description=phrases["approve"])
@describe(member=phrases["approved_member"])
async def approve(interaction: Interaction, member: Member,
                  org: discord.app_commands.Transform[database.Org, ApprovableOrganisation]):
    user, org = await ensure_user_waiting_approval(interaction, member, org)
    if not user:
        return
    if not ensure_author_permissions(interaction.user, user):
        message = phrases["invalid_approval_permissions"].format(user.user_id, user.orgs[0].org.org_id,
                                                                 interaction.user.id)
        return await interaction.response.send_message(message, ephemeral=True)
    user.orgs[0].permission_level = 1
    database.update_user(user)
    await member.edit(nick=user.nick)
    await member.add_roles(interaction.guild.get_role(org.org_id))
    await interaction.response.send_message(phrases["user_approved"].format(user.user_id, org.org_id),
                                            ephemeral=True)
    await member.send(phrases["approved_dm"].format(interaction.guild.name, org.name))


@bot.tree.command(description=phrases["reject"])
@describe(member=phrases["rejected_member"])
async def reject(interaction: Interaction, member: Member,
                 org: discord.app_commands.Transform[database.Org, ApprovableOrganisation]):
    user, org = await ensure_user_waiting_approval(interaction, member, org)
    if not user:
        return
    if not ensure_author_permissions(interaction.user, user):
        message = phrases["invalid_reject_permissions"].format(user.user_id, org.org_id, interaction.user.id)
        return await interaction.response.send_message(message, ephemeral=True)
    database.delete_user(user)
    message = phrases["user_rejected"].format(user.user_id, org.org_id)
    await interaction.response.send_message(message, ephemeral=True)
    await member.send(phrases["rejected_dm"].format(interaction.guild.name, org.name))


async def ensure_user_waiting_approval(interaction: Interaction, member: Member, org: database.Org) \
        -> (database.User, database.Org):
    user = database.get_user(member.id)
    approvable_orgs: {int, database.OrgPermissions} = {org.org.org_id: org for org in user.orgs}
    if org.org_id not in approvable_orgs:
        await interaction.response.send_message(phrases["need_org_to_approve"], ephemeral=True)
    elif approvable_orgs[org.org_id].permission_level > 0:
        message = phrases["already_registered"].format(member.user_id, org.org_id)
        await interaction.response.send_message(message, ephemeral=True)
    else:
        return user, approvable_orgs[org.org_id].org


@bot.tree.command(description=phrases["join"])
async def join(interaction: Interaction, org: discord.app_commands.Transform[database.Org, JoinableOrganisation]):
    member = interaction.guild.get_member(interaction.user.id)
    user = database.get_user(member.id)
    if not (user.nick and interaction.guild.get_member(user.user_id).nick):
        await interaction.response.send_modal(GiveNameModal(org))
    else:
        await send_join_application(interaction, org, user.nick)


class GiveNameModal(discord.ui.Modal, title=phrases["give_name_modal_title"]):

    def __init__(self, org: database.Org):
        self._org = org
        super().__init__()

    name = discord.ui.TextInput(label=phrases["join_modal_name_label"],
                                placeholder=phrases["join_modal_name_placeholder"])

    async def on_submit(self, interaction: Interaction):
        await send_join_application(interaction, self._org, self.name.value)

    async def on_error(self, interaction: Interaction, error: Exception):
        await interaction.response.send_message(phrases["error"], ephemeral=True)


async def send_join_application(interaction: Interaction, org: database.Org, name: str):
    user = database.get_user(interaction.user.id)
    admin_channel = bot.get_channel(settings["admin_channel_id"])
    user.orgs.append(database.OrgPermissions(org))
    if name != user.nick:
        user.nick = name
    register_info = phrases["pending_registration"].format(user.user_id, user.nick, org.org_id)
    approval_instructions = phrases["approval_instructions"].format("/", "approve", "/", "reject")
    database.update_user(user)
    await admin_channel.send(f"{register_info} {approval_instructions}")
    message = phrases["awaiting_approval"].format(org.name, name)
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(description=phrases["add"])
@describe(member=phrases["adding_member"], org=phrases["adding_org"])
@is_bot_admin()
async def add(interaction: Interaction, member: Member,
              org: discord.app_commands.Transform[database.Org, AddableOrganisation]):
    user = database.get_user(member.id)
    role: Role = org.role
    if role.id in [user_org.org.org_id for user_org in user.orgs]:
        return await interaction.response.send_message(phrases["already_registered"].format(member.user_id, role.id),
                                                       ephemeral=True)
    user_org = database.OrgPermissions(database.get_org(role.id), 1)
    user.orgs.append(user_org)
    database.update_user(user)
    await member.add_roles(interaction.guild.get_role(role.id))
    await interaction.response.send_message(phrases["org_joined"].format(user.user_id, org.org_id),
                                            ephemeral=True)


@bot.tree.command(description=phrases["leave"])
@describe(member=phrases["leaving_member"], org=phrases["leaving_org"])
@is_bot_admin()
async def remove(interaction: Interaction, member: Member,
                 org: discord.app_commands.Transform[database.Org, RemovableOrganisation]):
    user = database.get_user(member.id)
    role: Role = org.role
    deleted_org = next((user_org for user_org in user.orgs), None)
    if not deleted_org:
        return await interaction.response.send_message(phrases["not_org_member"].format(member.id, role.id),
                                                       ephemeral=True)
    database.delete_user_org(deleted_org, user.user_id)
    await member.remove_roles(role)
    await interaction.response.send_message(phrases["org_left"].format(user.user_id, role.id), ephemeral=True)


class RegisterCommands(Enum):
    guild = 0
    admin_channel = 1


@bot.tree.command(name="register-admin-channel", description=phrases["register"])
async def register_admin_channel(interaction: Interaction):
    settings["admin_channel_id"] = interaction.channel.id
    with open("persistence/settings.json", "w", encoding="utf-8") as settings_out:
        json.dump(settings, settings_out)
    await interaction.response.send_message(phrases["admin_channel_registered"].format(interaction.channel.id),
                                            ephemeral=True)


class Permissions(Enum):
    admin = 3
    moderator = 2
    user = 1


async def set_channel_permissions(guild: Guild, member: Member, permission_level: int):
    admin_channel = guild.get_channel(settings["admin_channel_id"])
    if permission_level < 2:
        return await admin_channel.set_permissions(member, overwrite=None)
    await admin_channel.set_permissions(member, read_messages=True, send_messages=True)


@bot.tree.command(description=phrases["permissions"])
@describe(member=phrases["permissions_member"], org=phrases["permissions_org"],
          permission_level=phrases["permissions_level"])
@is_bot_admin()
async def permissions(interaction: Interaction, member: Member,
                      org: discord.app_commands.Transform[database.Org, RemovableOrganisation],
                      permission_level: Permissions):
    permission = permission_level.value
    if not org:
        return await interaction.response.send_message(phrases["role_is_not_org"].format(org.role.name), ephemeral=True)
    user = database.get_user(member.id)
    user_org = next((org_permissions for org_permissions in user.orgs if org_permissions.org.org_id == org.org_id),
                    None)
    if not user_org:
        message = phrases["user_not_org_member"].format(member.id, user_org.org.org_id)
        return await interaction.response.send_message(message, ephemeral=True)
    user_org.permission_level = permission_level.value
    max_permissions = max(permission, *[org_permissions.permission_level for org_permissions in user.orgs])
    await set_channel_permissions(interaction.guild, member, max_permissions)
    database.update_user(user)
    message = phrases["permissions_updated"].format(member.id, org.org_id, permission_level.name)
    await interaction.response.send_message(message, ephemeral=True)


if __name__ == '__main__':
    database.init_databases()
    token = get_env_or_file("TOKEN")
    bot.run(token)
