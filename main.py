#!/usr/bin/env python3

"""
greenlight_cal/main.py: Sync Green Light Cinema showtimes to Google GCal.

Create a calendar:
  python main.py --credentials_file path/to/credentials.json --create_calendar > calendar_id.txt

Add a user to the calendar ACL:
  python main.py --credentials_file path/to/credentials.json --calendar_id $(< calendar_id.txt) --add_writer username@gmail.com

NOTE: The credentials file can be omitted if the CREDENTIALS_FILE environment
variable is set to the path of the credentials file. Similarly, the calendar
ID can be omitted if the CALENDAR_ID environment variable is set. A .env file
can be used to set these environment variables for convenience.

Inspect the calendar ACL:
  python main.py --print_acl

Update the calendar with current showtimes:
  python main.py --diff

Environment variables:

 - CALENDAR_ID - The ID of the calendar.
 - CREDENTIALS_FILE - The location of the credentials file for the service
   account.
 - CREDENTIALS_JSON - The credentials JSON file contents.

See --help for more options.
"""

from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Self, ClassVar, cast
from urllib.error import URLError
import argparse
import gzip
import io
import json
import os
import re
import sys
import tempfile
import urllib.request

from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv

from google_cal import eprint, GCal, CalEvent, GenericError, CalDateTime

# https://en.wikipedia.org/wiki/ISO_8601#Durations
units = {'H': 3600, 'M': 60, 'S': 1}

url = "https://ticketing.useast.veezi.com/sessions/?siteToken=kegxkyy004b7bm6apwhtgcm274"


@dataclass
class Context:
  """Singleton context for shared state."""
  calendar: GCal
  cli_args: Any
  instance: ClassVar[Self|None] = None
  now: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

  dry_run: bool = False
  calendar_file: str|None = None
  showtimes_file: str|None = None
  showtimes_html_file: str|None = None

  def __post_init__(self):
    self.__class__.instance = self
    for field in fields(self.__class__):
      if hasattr(self.cli_args, field.name):
        setattr(self, field.name, getattr(self.cli_args, field.name))


@dataclass
class ShowtimeListing:
  """Listings read from the showtimes webpage"""
  title: str
  desc: str
  rating: str
  rating_desc: str


def tag_to_text(tag: Tag|None) -> str:
  """
  HTML tag to plain text, with normalized newlines.
  
  Used primarily for showtime movie descriptions as they often contain hard
  line wraps.
  """
  if tag is None:
    return ''
  text = tag.get_text(separator=' ', strip=True)
  text = re.sub(r'(?:[^\n])\n(?:[^\n])', ' ', text)
  text = re.sub(r'[ ]+', ' ', text)
  return text


def calendar_get_acls(ctx, cal):
  if ctx.dry_run:
    eprint('dry run prevented fetching acls')
    return []
  acls = cal.get_acls()
  print(json.dumps([
    f'{acl["scope"]["type"]}/{acl["role"]}: {acl["scope"].get("value", "")}'
    for acl in acls
  ], indent=2))


def read_calendar_events(ctx: Context, cal: GCal) -> list[CalEvent]:
  """Read events from the calendar."""
  if ctx.calendar_file:
    with open(ctx.calendar_file, 'r') as f:
      data = json.load(f)
    return [CalEvent.from_dict(item) for item in data]
  if ctx.dry_run:
    raise GenericError("Error: no calendar file provided for dry run.")
  return cal.read_events()


def delete_events(ctx: Context, cal: GCal, event_ids: list[str]):
  """Remove events with the given IDs from the calendar."""
  if ctx.dry_run:
    eprint(f"Dry run: would delete events: {json.dumps(event_ids, indent=2)}")
    return

  cal.delete_events(event_ids)


def write_events(ctx: Context, cal: GCal, events: list[CalEvent]):
  """Publish events to the calendar."""
  if ctx.dry_run:
    eprint(f"Dry run: would write events: {json.dumps([str(e) for e in events], indent=2)}")
    return events
  return cal.write_events(events)


def read_showtimes(ctx: Context) -> list[CalEvent]:
  """Read showtimes from the listing site."""
  if ctx.showtimes_file:
    with open(ctx.showtimes_file, 'r') as f:
      data = json.load(f)
    return [CalEvent.from_dict(item) for item in data]
  html = load_listing_site(ctx)
  return parse_showtimes(html)


def load_listing_site(ctx: Context) -> str:
  if ctx.showtimes_html_file:
    with open(ctx.showtimes_html_file, 'r') as f:
      return f.read()
  if ctx.dry_run:
    raise GenericError("Error: no showtimes JSON or HTML file provided for dry run. Provide one via --showtimes_file or --showtimes_html_file argument.")
  headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
  }
  try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as res:
      if res.getheader('Content-Encoding') == 'gzip':
        with io.BytesIO(res.read()) as buf:
          with gzip.GzipFile(fileobj=buf) as f:
            return f.read().decode()
      return res.read()
  except URLError as e:
    raise GenericError(f"Error fetching {url}: {e}")


def parse_showtimes(html_src: str) -> list[CalEvent]:
  """Given the HTML of the showtimes page, return a list of calendar events."""
  listings: list[CalEvent] = []
  films: dict[str, ShowtimeListing] = {}
  parser = BeautifulSoup(html_src, 'html.parser')
  for film in parser.select('#sessionsByFilmConent .film'):
    film_data = {}
    censor = film.select_one('.censor')
    data = ShowtimeListing(
      title = tag_to_text(film.select_one('.title')),
      desc = tag_to_text(film.select_one('.film-desc')),
      rating = tag_to_text(censor),
      rating_desc = tag_to_text(censor.parent) if censor else '',
    )
    films[data.title] = data
    if data.rating_desc == 'NR':
      data.rating_desc = 'This film is Not Rated.'

  for script in parser.find_all('script', {'type': 'application/ld+json'}):
    # [{
    #   "@type":"VisualArtsEvent",
    #   "startDate":"2026-01-28T14:00:00-05:00",
    #   "duration":"PT1H30M",
    #   "location":{
    #     "@type":"Place",
    #     "address":"221 2nd Avenue North, St. Petersburg, Florida, 33701, USA",
    #     "name":"Green Light Cinema"
    #   },
    #   "name":"You Got Gold: A Celebration of John Prine",
    #   "url":"https://ticketing.useast.veezi.com/purchase/3192?siteToken=kegxkyy004b7bm6apwhtgcm274",
    #   "@context":"http://schema.org"
    # }, ...]
    try:
      obj = json.loads(script.string or '[]')
    except json.JSONDecodeError as e:
      # bad json
      eprint(f"Error decoding JSON: {e}")
      continue
    if not isinstance(obj, list):
      # not a list of showtimes
      continue
    for item in obj:
      if not isinstance(item, dict):
        # not a showtime
        continue
      try:
        if item.get('@type') != 'VisualArtsEvent':
          # not a showtime
          continue
        showtime = films[item['name']]
        endDate = datetime.fromisoformat(item['startDate'])
        for m in re.findall(r'(\d+)([A-Z])', item['duration']):
          endDate += timedelta(seconds=int(m[0]) * units[m[1]])
        listings.append(CalEvent(
            kind="calendar#event",
            summary=f"{item['name']} @ {item['location']['name']}",
            location=item['location']['address'],
            start=CalDateTime(dateTime=item['startDate']),
            end=CalDateTime(dateTime=endDate.isoformat()),
            description=f"{item['url']}\n\n{showtime.desc}\n\nRating: {showtime.rating_desc}",
        ))
      except KeyError as e:
        GenericError(f"KeyError processing item: {item}, error: {e}")
  return listings


def update_events(ctx: Context, cal: GCal, cal_events: list[CalEvent], showtimes: list[CalEvent]) -> None:
  """Diff published and fresh showtimes."""
  to_create: list[CalEvent] = []
  to_delete: list[str] = []
  cal_map = {e.hash: e for e in cal_events}
  st_map = {e.hash: e for e in showtimes}
  start_time = min([e.start.get_time() for e in showtimes])
  end_time = max([e.start.get_time() for e in showtimes])
  eprint('Start time:', start_time)
  eprint('End time:', end_time)
  for key, event in st_map.items():
    dt = event.start.get_time()
    if dt < start_time > dt  or dt > end_time:
      # skip events outside our window
      continue
    if key not in cal_map:
      eprint(f'Adding event {event}')
      to_create.append(event)
  for key, event in cal_map.items():
    dt = event.start.get_time()
    if dt < start_time or dt > end_time:
      # skip events outside our window
      continue
    if key not in st_map:
      eprint(f'Deleting event {event}')
      to_delete.append(event.id or '')
  eprint(f'Successfully wrote events: {json.dumps([
    e.htmlLink for e in write_events(ctx, cal, to_create)])}')
  delete_events(ctx, cal, to_delete)
  eprint(f'Deleted {len(to_delete)} events')


def main(nargs: list[str]):
  parser = argparse.ArgumentParser()
  # global arguments
  parser.add_argument('--calendar_id', type=str, help='The ID of the calendar to use. Can be omitted if CALENDAR_ID environment variable is set or specified in .env file.')
  parser.add_argument('--credentials_file', type=str, help='Path to the service account credentials JSON file. Can be omitted if CREDENTIALS_FILE environment variable is set or specified in .env file.')

  # Testing arguments
  parser.add_argument('--dry_run', action='store_true', help='Run in dry-run mode. No external calls will be made.')
  parser.add_argument('--calendar_file', type=str, help='(Testing) Path to a JSON file containing calendar events.')
  parser.add_argument('--showtimes_file', type=str, help='(Testing) Path to a JSON file containing showtimes data.')
  parser.add_argument('--showtimes_html_file', type=str, help='(Testing) Path to an HTML file containing showtimes page data.')

  # Manage calendars
  parser.add_argument('--list_calendars', action='store_true', help="List all calendars the credentialed user has access to.")
  parser.add_argument('--create_calendar', type=str, help='Create a new calendar and print its ID.')
  parser.add_argument('--delete_calendar', action='store_true', help='Delete the specified calendar.')
  parser.add_argument('--print_acl', action='store_true', help='Print the ACL of the calendar.')
  parser.add_argument('--add_writer', type=str, help='Add a user to the calendar ACL with the given email address.')
  parser.add_argument('--remove_writer', type=str, help='Remove a user from the calendar ACL with the given email address.')
  parser.add_argument('--add_owner', type=str, help='Add an owner to the calendar ACL with the given email address.')
  parser.add_argument('--remove_owner', type=str, help='Remove an owner from the calendar ACL with the given email address.')

  # Manage events
  parser.add_argument('--read_calendar', action='store_true', help='Read and print existing calendar events.')
  parser.add_argument('--read_showtimes', action='store_true', help='Read and print existing showtimes.')
  parser.add_argument('--delete', nargs='+', help='Delete calendar events with the given IDs.')
  parser.add_argument('--update', action='store_true', help='Diff existing calendar events with current showtimes and update accordingly.')
  parser.add_argument('--clear', action='store_true', help='Clear all existing calendar events.')

  args, extra = parser.parse_known_args(nargs)
  if extra:
    raise GenericError(f"Error: unrecognized arguments: {extra}")
  
  dry_run = args.dry_run
  calendar_id = args.calendar_id or os.getenv('CALENDAR_ID') or None
  credentials_file = args.credentials_file or os.getenv('CREDENTIALS_FILE') or None
  
  credentials_json = os.getenv('CREDENTIALS_JSON')
  
  temp_file = tempfile.NamedTemporaryFile(delete_on_close=False)
  try:
    if credentials_json:
      credentials_file = temp_file.name
      with open(credentials_file, 'wb') as fp:
        fp.write(credentials_json.encode())
    execute(calendar_id, credentials_file, dry_run, args)
  finally:
    os.remove(temp_file.name)


def execute(calendar_id: str|None, credentials_file: str|None, dry_run: bool, args: Any):
  cal = GCal(
    calendar_id = calendar_id if not dry_run else None,
    credentials_file = credentials_file if not dry_run else None,
    dry_run = dry_run,
  )
  ctx = Context(calendar=cal, cli_args=args)

  if dry_run:
    eprint('=== DRY RUN ENABLED ===')

  if args.list_calendars:
    print(json.dumps(cal.list_calendars(), indent=2))
  if args.create_calendar is not None:
    print(cal.create_calendar(
      args.create_calendar or 'Green Light Cinema Showtimes'))
  if args.delete_calendar:
    cal.delete_calendar()
  if args.add_writer:
    cal.add_writer(args.add_writer)
  if args.remove_writer:
    cal.remove_writer(args.remove_writer)
  if args.add_owner:
    cal.add_owner(args.add_owner)
  if args.remove_owner:
    cal.remove_owner(args.remove_owner)
  if not all((arg is None or arg == False for arg in [
    args.list_calendars, args.create_calendar, args.delete_calendar,
    args.add_writer, args.remove_writer, args.add_owner, args.remove_owner
  ])):
    return


  if args.print_acl:
    calendar_get_acls(ctx, cal)
    return
  if args.read_calendar:
    print(json.dumps([
      asdict(e) for e in read_calendar_events(ctx, cal)
    ], indent=2))
    return
  if args.read_showtimes:
    st_events = read_showtimes(ctx)
    print(json.dumps([asdict(e) for e in st_events], indent=2))
    return
  if args.delete:
    delete_events(ctx, cal, args.delete)
    return
  if args.update:
    st_events = read_showtimes(ctx)
    cal_events = read_calendar_events(ctx, cal)
    update_events(ctx, cal, cal_events, st_events)
    return
  if args.clear:
    cal_events = read_calendar_events(ctx, cal)
    delete_events(ctx, cal, event_ids=[e.id or '' for e in cal_events])
    return
  eprint(__doc__)

if __name__ == "__main__":
  load_dotenv()
  try:
    main(sys.argv[1:])
  except GenericError as e:
    eprint(e)
    sys.exit(1)
