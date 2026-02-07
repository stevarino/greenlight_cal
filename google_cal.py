#!/usr/bin/env python3

"""
calendar.py: Interface that supports Google Calendar operations.
"""

from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from typing import Self
import hashlib
import json
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


class GenericError(Exception):
  def __init__(self, message: str):
    super().__init__(message)
    self.message = message

  def __str__(self):
    return self.message


class CredentialsNotInitializedError(GenericError):
  def __init__(self):
    super().__init__("Error: credentials not initialized.")
  

class CalendarIDNotInitializedError(GenericError):
  def __init__(self):
    super().__init__("Error: no Calendar ID provided.")


def eprint(*args, **kwargs):
  """Print to stderr."""
  print(*args, file=sys.stderr, **kwargs)


@dataclass
class CalDateTime:
  dateTime: str
  timeZone: str = 'UTC'

  def get_time(self) -> datetime:
    return datetime.fromisoformat(self.dateTime)
  
  @classmethod
  def from_datetime(cls, dt: datetime, tz: str|None = None) -> Self:
    return cls(dateTime=dt.isoformat(), timeZone=tz or 'UTC')


hash_cache: dict[int, str] = {}


@dataclass
class CalEvent:
  kind: str
  summary: str
  start: CalDateTime
  end: CalDateTime
  id: str|None = None
  etag: str|None = None
  htmlLink: str|None = None
  location: str|None = None
  description: str|None = None
  attachments: list[dict]|None = None
  
  @classmethod
  def from_dict(cls, data: dict) -> Self:
    return cls(**{
      field.name: data[field.name]
      for field in fields(cls)
      if field.name in data
    })

  def __post_init__(self):
    if type(self.start) == dict:
      self.start = CalDateTime(**self.start)
    if type(self.end) == dict:
      self.end = CalDateTime(**self.end)

  def __str__(self):
    return f"{self.summary} @ {self.start.get_time().strftime('%b %d %H:%M')}"

  @property
  def hash(self) -> str:
    """Get a hash of the event, computing it if necessary."""
    if not hash_cache.get(id(self)):
      hash_cache[id(self)] = hashlib.sha1(str(self).encode()).hexdigest()
    return hash_cache[id(self)]


class GCal:
  """Interface for interacting with Google Calendar."""
  def __init__(self, calendar_id: str|None, credentials_file: str|None, dry_run: bool = False):
    self._calendar_id = calendar_id
    self.credentials_file = credentials_file
    self.dry_run = dry_run
    self.now = datetime.now(tz=timezone.utc)
    self._service = None
    if dry_run or not credentials_file:
      return
    creds = Credentials.from_service_account_file(credentials_file)
    self._service = build("calendar", "v3", credentials=creds)
  

  @property
  def service(self):
    if self._service is None:
      raise CredentialsNotInitializedError()
    return self._service


  @property
  def calendar_id(self):
    if self._calendar_id is None:
      raise CalendarIDNotInitializedError()
    return self._calendar_id


  def read_events(self) -> list[CalEvent]:
    """Read events from the calendar."""
    if self.dry_run:
      eprint('dry run - no events to read')
      return []
    items = self.service.events().list(
      calendarId=self.calendar_id,
      timeMin=self.now.isoformat(),
      singleEvents=True,
      orderBy='startTime'
    ).execute().get('items', [])
    return [CalEvent.from_dict(item) for item in items]  


  def delete_events(self, event_ids: list[str]):
    """Remove events with the given IDs from the calendar."""
    if self.dry_run:
      eprint('dry run - no events to delete')
      return
    for event_id in event_ids:
       self.service.events().delete(
        calendarId=self.calendar_id,
        eventId=event_id,
      ).execute()
    eprint(f"Events deleted: {', '.join(event_ids)}")


  def write_events(self, events: list[CalEvent]) -> list[CalEvent]:
    """Publish events to the calendar."""
    if self.dry_run:
      eprint('dry run - no events to write')
      return events
    new_events = []
    for event in events:
      new_events.append(self.service.events().insert(
        calendarId=self.calendar_id,
        body=asdict(event),
        supportsAttachments=True,
      ).execute())
    return [CalEvent.from_dict(e) for e in new_events]


  def create_calendar(self, description) -> str:
    """Create a new calendar and print its ID."""
    if self.dry_run:
      eprint("Dry run: would create calendar and print its ID")
      return ''
    created_calendar = self.service.calendars().insert(body={
      'summary': description,
    }).execute()
    self._calendar_id = created_calendar['id']
    eprint(f"Calendar created with ID: {self._calendar_id}")
    self._insert_acl({
      "role": "reader",
      "scope": {"type": "default"}
    })
    eprint("Calendar ACL updated to allow public read access")
    return self._calendar_id


  def get_acls(self):
    """Get the ACL of the calendar."""
    if self.dry_run:
      eprint("Dry run: would get ACL of calendar")
      return []
    acl = self.service.acl().list(calendarId=self.calendar_id).execute()
    return acl.get('items', [])


  def _insert_acl(self, acl):
    """Insert an ACL rule."""
    if self.dry_run:
      eprint(f"Dry run: would insert ACL rule: {json.dumps(acl, indent=2)}")
      return {}
    created_acl = self.service.acl().insert(calendarId=self.calendar_id, body=acl).execute()
    eprint(f"ACL rule inserted: {json.dumps(created_acl, indent=2)}")
    return created_acl


  def _delete_acl(self, rule_id):
    """Delete an ACL rule."""
    if self.dry_run:
      eprint(f"Dry run: would delete ACL rule with ID: {rule_id}")
      return
    self.service.acl().delete(calendarId=self.calendar_id, ruleId=rule_id).execute()
    eprint(f"ACL rule deleted: {rule_id}")


  def add_writer(self, email: str):
    """Add a user to the calendar ACL with the given email address."""
    created_rule = self._insert_acl({
      'scope': {
        'type': 'user',
        'value': email,
      },
      'role': 'writer',
    })
    eprint(f"User {email} added to ACL with role writer {created_rule}")


  def remove_writer(self, email: str):
    """Remove a user from the calendar ACL with the given email address."""
    if self.dry_run:
      eprint(f"Dry run: would remove user {email} from calendar ACL")
      return
    acls = self.get_acls()
    for acl in acls:
      scope = acl['scope']
      if scope['type'] == 'user' and scope['value'] == email:
        rule_id = acl['id']
        self._delete_acl(rule_id)
        eprint(f"User {email} removed from ACL")
        return
    raise GenericError(f"User {email} not found in ACL")


  def add_owner(self, email: str):
    """Add an owner to the calendar ACL with the given email address."""
    created_rule = self._insert_acl({
      'scope': {
        'type': 'user',
        'value': email,
      },
      'role': 'owner',
    })
    eprint(f"User {email} added to ACL with role owner {created_rule}")


  def remove_owner(self, email: str):
    """Remove an owner from the calendar ACL with the given email address."""
    if self.dry_run:
      eprint(f"Dry run: would remove owner {email} from calendar ACL")
      return
    acls = self.get_acls()
    for acl in acls:
      scope = acl['scope']
      if scope['type'] == 'user' and scope['value'] == email and acl['role'] == 'owner':
        rule_id = acl['id']
        self._delete_acl(rule_id)
        eprint(f"Owner {email} removed from ACL")
        return
    raise GenericError(f"Owner {email} not found in ACL")

