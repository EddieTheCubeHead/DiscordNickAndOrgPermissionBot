import os
import sqlite3
from enum import Enum
from typing import Union

from discord import Role

con = sqlite3.connect("persistence/bot_db.sqlite")


class DbEntryStatus(Enum):
    NEW = 0
    CHANGED = 1
    UNCHANGED = 2


class Org:
    def __init__(self, org_id: int, name: str, status: DbEntryStatus = DbEntryStatus.UNCHANGED):
        self._org_id = org_id
        self._name = name
        self.status = status
        self.role: Role | None = None

    def __repr__(self):
        return f"Org '{self.name}', ID: {self.org_id}"

    @property
    def org_id(self):
        return self._org_id

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name: str):
        self.status = DbEntryStatus.CHANGED
        self._name = name


class OrgPermissions:
    def __init__(self, org: Org, permission_level: int = 0, status: DbEntryStatus = DbEntryStatus.NEW):
        self.org = org
        self._permission_level = permission_level
        self.status = status

    def __repr__(self):
        return f"OrgUser Org: '{self.org.name}', Permission level '{self.permission_level}'"

    @property
    def permission_level(self):
        return self._permission_level

    @permission_level.setter
    def permission_level(self, permission_level: int):
        self.status = DbEntryStatus.CHANGED
        self._permission_level = permission_level


class User:
    def __init__(self, user_id: int, nick: str = None, *orgs: OrgPermissions,
                 status: DbEntryStatus = DbEntryStatus.UNCHANGED):
        self._user_id = user_id
        self._nick = nick
        self.orgs: list[OrgPermissions] = list(orgs)
        self.status = status

    def __repr__(self):
        return f"User {self.user_id}, Nick: '{self.nick}', Orgs: {self.orgs}"

    @property
    def user_id(self):
        return self._user_id

    @user_id.setter
    def user_id(self, user_id: int):
        self.status = DbEntryStatus.CHANGED
        self._user_id = user_id

    @property
    def nick(self):
        return self._nick

    @nick.setter
    def nick(self, nick: str):
        self.status = DbEntryStatus.CHANGED
        self._nick = nick


def init_databases():
    for table in ("orgs", "users", "org_users"):
        with open(os.path.join("sqlscripts", f"create_{table}.sql"), "r", encoding="utf-8") as sql_script:
            con.execute(sql_script.read())


def add_user(user_id: int):
    con.execute("INSERT INTO Users (ID) VALUES (?)", (user_id,))
    con.commit()


def get_user(member_id: int) -> User:
    if con.execute("SELECT EXISTS(SELECT 1 FROM Users WHERE Users.ID = ?)", (member_id,)).fetchone()[0] == 0:
        add_user(member_id)
    data = con.execute("SELECT ID, Nick FROM Users WHERE Users.ID = ?", (member_id,)).fetchone()
    org_data = con.execute("SELECT OrgID, PermissionLevel FROM OrgUsers WHERE OrgUsers.UserID = ?", (member_id,))\
        .fetchall()
    orgs = [get_org(data_point[0]) for data_point in org_data]
    org_permissions = [OrgPermissions(org, data_point[1], DbEntryStatus.UNCHANGED) for org, data_point in zip(orgs, org_data)]
    return User(*data, *org_permissions)


def update_user(user: User):
    if user.status == DbEntryStatus.CHANGED:
        con.execute("UPDATE Users SET Nick = ? WHERE ID = ?", (user.nick, user.user_id))
    for org_user in user.orgs:
        if org_user.status == DbEntryStatus.NEW:
            con.execute("INSERT INTO OrgUsers (OrgID, UserID, PermissionLevel) VALUES (?, ?, ?)",
                        (org_user.org.org_id, user.user_id, org_user.permission_level))
        elif org_user.status == DbEntryStatus.CHANGED:
            con.execute("UPDATE OrgUsers SET PermissionLevel = ? WHERE OrgID = ? AND UserID = ?",
                        (org_user.permission_level, org_user.org.org_id, user.user_id))

    con.commit()


def delete_user(user: User):
    con.execute("DELETE FROM Users WHERE ID = ?", (user.user_id,))
    con.commit()


def get_org_names() -> list[str]:
    rows = con.execute("SELECT Name FROM Orgs")
    return [row[0] for row in rows]


def org_exists(org_name: str):
    return con.execute("SELECT EXISTS(SELECT 1 FROM Orgs WHERE Orgs.Name = ?)", (org_name,)).fetchone()[0] == 1


def add_org(org: Org):
    con.execute("INSERT INTO Orgs (ID, Name) VALUES (?, ?)", (org.org_id, org.name))
    con.commit()


def get_org(data: Union[str, int]):
    if type(data) == str:
        org_data = con.execute("SELECT ID, Name FROM Orgs WHERE Orgs.Name = ?", (data,)).fetchone()
    else:
        org_data = con.execute("SELECT ID, Name FROM Orgs WHERE Orgs.ID = ?", (data,)).fetchone()

    if not org_data:
        return None
    return Org(*org_data)


def delete_user_org(user_org: OrgPermissions, user_id: int):
    con.execute("DELETE FROM OrgUsers WHERE OrgID = ? AND UserID = ?", (user_org.org.org_id, user_id))
