#!/usr/bin/env python3

"""
greenlight_cal/main.py: Sync Green Light Cinema showtimes to Google GCal.

Create a calendar:
  python main.py --credentials_file path/to/credentials.json \\
    calendar --create > calendar_id.txt

Add a user to the calendar ACL:
  python main.py --credentials_file path/to/credentials.json \\
    --calendar_id $(< calendar_id.txt) \\
    calendar --add_writer username@gmail.com

NOTE: The credentials file can be omitted if the CREDENTIALS_FILE environment
variable is set to the path of the credentials file. Similarly, the calendar
ID can be omitted if the CALENDAR_ID environment variable is set. A .env file
can be used to set these environment variables for convenience.

Inspect the calendar ACL:
  python main.py calendar --acls

Update the calendar with current showtimes:
  python main.py events --update

Environment variables:

 - CALENDAR_ID - The ID of the calendar.
 - CREDENTIALS_FILE - The location of the credentials file for the service
   account.
 - CREDENTIALS_JSON - The credentials JSON file contents.

See --help for more options.
"""

from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Self, ClassVar, Callable, Sequence, cast, NewType
from urllib.error import URLError
import argparse
import gzip
import io
import json
import os
import re
import sys
import urllib.request

from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv

from google_cal import eprint, GCal, CalEvent, GenericError, CalDateTime

# https://en.wikipedia.org/wiki/ISO_8601#Durations
units = {'H': 3600, 'M': 60, 'S': 1}

# Green Light Cinema Showtimes Page
SHOWTIMES_URL = "https://ticketing.useast.veezi.com/sessions/?siteToken=kegxkyy004b7bm6apwhtgcm274"
DEFAULT_CAL_TITLE = 'Green Light Cinema Showtimes'


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


def pjson(json_data: Any, **kwargs):
  """Pretty JSON Formatter"""
  return json.dumps(json_data, indent=2, **kwargs)


def ppjson(json_data: Any, **kwargs):
  """Pretty JSON Printer"""
  print(pjson(json_data, **kwargs))


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


def calendar_get_acls(cal: GCal):
  acls = cal.get_acls()
  ppjson([
    f'{acl["scope"]["type"]}/{acl["role"]}: {acl["scope"].get("value", "")}'
    for acl in acls
  ])


def read_calendar_events(ctx: Context, cal: GCal) -> list[CalEvent]:
  """Read events from the calendar."""
  if ctx.calendar_file:
    with open(ctx.calendar_file, 'r') as f:
      data = json.load(f)
    return [CalEvent.from_dict(item) for item in data]
  if ctx.dry_run:
    raise GenericError("Error: no calendar file provided for dry run.")
  return cal.read_events()


def print_calendar_events(ctx: Context, cal: GCal) -> None:
  ppjson([asdict(e) for e in read_calendar_events(ctx, cal)])


def delete_events(ctx: Context, cal: GCal, event_ids: list[str]):
  """Remove events with the given IDs from the calendar."""
  if ctx.dry_run:
    eprint(f"Dry run: would delete events: {pjson(event_ids)}")
    return

  cal.delete_events(event_ids)


def write_events(ctx: Context, cal: GCal, events: list[CalEvent]):
  """Publish events to the calendar."""
  if ctx.dry_run:
    eprint(f"Dry run: would write events: {pjson([str(e) for e in events])}")
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


def print_showtimes(ctx: Context) -> None:
  ppjson([asdict(e) for e in read_showtimes(ctx)])


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
    req = urllib.request.Request(SHOWTIMES_URL, headers=headers)
    with urllib.request.urlopen(req) as res:
      if res.getheader('Content-Encoding') == 'gzip':
        with io.BytesIO(res.read()) as buf:
          with gzip.GzipFile(fileobj=buf) as f:
            return f.read().decode()
      return res.read()
  except URLError as e:
    raise GenericError(f"Error fetching {SHOWTIMES_URL}: {e}")


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
      if not isinstance(item, dict) or item.get('@type') != 'VisualArtsEvent':
        # not a showtime
        continue
      try:
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


def update_events(ctx: Context, cal: GCal) -> None:
  """
  Diff published and fresh showtimes.
  
  NOTE: will only update calenar entries within the time window of the
  showtimes listings.
  """
  cal_events = read_calendar_events(ctx, cal)
  showtimes = read_showtimes(ctx)

  to_create: list[CalEvent] = []
  to_delete: list[CalEvent] = []
  cal_map = {e.hash: e for e in cal_events}
  st_map = {e.hash: e for e in showtimes}
  start_time = min([e.start.get_time() for e in showtimes])
  end_time = max([e.start.get_time() for e in showtimes])
  for key, event in st_map.items():
    dt = event.start.get_time()
    if dt < start_time > dt  or dt > end_time:
      continue
    if key not in cal_map:
      to_create.append(event)
  for key, event in cal_map.items():
    dt = event.start.get_time()
    if dt < start_time or dt > end_time:
      continue
    if key not in st_map:
      to_delete.append(event)
  if to_create:
    eprint(f'added events: {pjson([
      f'{e} {e.htmlLink}' 
      for e in write_events(ctx, cal, to_create)
    ])}')
  if to_delete:
    delete_events(ctx, cal, [cast(str, e.id) for e in to_delete])
    eprint(f'deleted events: {pjson([str(e) for e in to_delete])}')

ActionFunc = Callable[[Context, GCal, str|Sequence[Any]|None], None]
def action_wrap(func: ActionFunc):
  """Returns an action class that runs a callback."""
  class WrappedAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
      setattr(namespace, 'action', lambda ctx, cal: func(ctx, cal, values))
  return WrappedAction


# Common add_argument settings support action callbacks
def arg_str(func: Callable[[Context, GCal, str], None]):
  """Argument callback that expects a string"""
  return {'type': str, 'action': action_wrap(cast(ActionFunc, func))}
def arg_list(func: Callable[[Context, GCal, list[str]], None]):
  """Argument callback that expects a list of strings"""
  return {'nargs': '+', 'action': action_wrap(cast(ActionFunc, func))}
def arg_zstr(func: Callable[[Context, GCal, str|None], None]): 
  """Argument callback that expects a string or None"""
  return {'nargs': '?', 'action': action_wrap(cast(ActionFunc, func))}
def arg_zed(func: Callable[[Context, GCal], None]): 
  """Argument callback that does not accept a value"""
  return {'nargs': 0, 'action': action_wrap(
    cast(ActionFunc, lambda x, c, n: func(x, c)))}
def arg_cal(func: Callable[[GCal], None]): 
  """Argument callback that only acceps a GCal argument"""
  return {'nargs': 0, 'action': action_wrap(
    cast(ActionFunc, lambda x, c, n: func(c)))}
def arg_ctx(func: Callable[[Context], None]):
  """Argument callback that only acceps a Context argument"""
  return {'nargs': 0, 'action': action_wrap(
    cast(ActionFunc, lambda x, c, n: func(x)))}


def calendar_argparse(calendar_parser: argparse.ArgumentParser):
  calendar_parser.add_argument(
    '--list', **arg_cal(lambda c: ppjson(c.list_calendars())),
    help="List all calendars the credentialed user has access to.")
  
  calendar_parser.add_argument(
    '--create', **arg_zstr(lambda x, c, n: (
      print(c.create_calendar(n or DEFAULT_CAL_TITLE)))),
    help='Create a new calendar and print its ID.')

  calendar_parser.add_argument(
    '--delete', **arg_cal(lambda c: c.delete_calendar()),
    help='Delete the specified calendar.')

  calendar_parser.add_argument(
    '--acls', **arg_cal(lambda c: calendar_get_acls(c)),
    help='Print the ACL of the calendar.')

  calendar_parser.add_argument(
    '--add_writer', **arg_str(lambda x, c, n: c.add_writer(n)),
    help='Add a user to the calendar ACL with the given email address.')

  calendar_parser.add_argument(
    '--remove_writer', **arg_str(lambda x, c, n: c.remove_writer(n)),
    help='Remove a user from the calendar ACL with the given email address.')

  calendar_parser.add_argument(
    '--add_owner', **arg_str(lambda x, c, n: c.add_owner(n)),
    help='Add an owner to the calendar ACL with the given email address.')

  calendar_parser.add_argument(
    '--remove_owner', **arg_str(lambda x, c, n: c.remove_owner(n)),
    help='Remove an owner from the calendar ACL with the given email address.')


def events_argparse(event_parser: argparse.ArgumentParser):
  event_parser.add_argument(
    '--read', **arg_zed(print_calendar_events),
    help='Read existing calendar events and print as JSON.')
  
  event_parser.add_argument(
    '--showtimes', **arg_ctx(print_showtimes),
    help='Read existing showtimes and print as JSON.')
  
  event_parser.add_argument(
    '--delete', **arg_list(lambda x, c, n: delete_events(x, c, n)),
    help='Delete calendar events with the given IDs.')
  
  event_parser.add_argument(
    '--update', **arg_zed(update_events),
    help='Diff existing calendar events with current showtimes and update accordingly.')
  
  event_parser.add_argument(
    '--clear', **arg_zed(lambda x, c: delete_events(x, c, event_ids=[
      e.id or '' for e in read_calendar_events(x, c)
    ])),
    help='Clear all existing calendar events.')


def main(nargs: list[str]):
  parser = argparse.ArgumentParser()
  # global arguments
  parser.add_argument(
    '--calendar_id', type=str,
    help='The ID of the calendar to use. Can be omitted if CALENDAR_ID environment variable is set or specified in .env file.')
  parser.add_argument(
    '--credentials_file', type=str,
    help='Path to the service account credentials JSON file. Can be omitted if CREDENTIALS_FILE environment variable is set or specified in .env file.')

  # Testing arguments
  tests = parser.add_argument_group('Testing Arguments')
  tests.add_argument(
    '--dry_run', action='store_true',
    help='Run in dry-run mode. No external calls will be made.')
  tests.add_argument(
    '--calendar_file', type=str,
    help='Path to a JSON file containing calendar events.')
  tests.add_argument(
    '--showtimes_file', type=str,
    help='Path to a JSON file containing showtimes data.')
  tests.add_argument(
    '--showtimes_html_file', type=str,
    help='(Testing) Path to an HTML file containing showtimes page data.')

  subparsers = parser.add_subparsers()
  calendar_argparse(subparsers.add_parser('calendar', aliases=['cal']))
  events_argparse(subparsers.add_parser('events', aliases=['ev']))

  args, extra = parser.parse_known_args(nargs)
  if extra:
    raise GenericError(f"Error: unrecognized arguments: {extra}")
  
  calendar_id = args.calendar_id or os.getenv('CALENDAR_ID') or None
  
  credentials = None
  if os.getenv('CREDENTIALS_JSON'):
    credentials = json.loads(cast(str, os.getenv('CREDENTIALS_JSON')))
  elif args.credentials_file:
    with open(args.credentials_file, 'r') as fp:
      credentials = json.loads(fp.read())
  elif os.getenv('CREDENTIALS_FILE'):
    with open(cast(str, os.getenv('CREDENTIALS_FILE')), 'r') as fp:
      credentials = json.loads(fp.read())

  cal = GCal(calendar_id, credentials, args.dry_run)
  ctx = Context(calendar=cal, cli_args=args)

  if args.dry_run:
    eprint('=== DRY RUN ENABLED ===')

  if hasattr(args, 'action'):
    args.action(ctx, cal)
  else:
    print(__doc__)


if __name__ == "__main__":
  load_dotenv()
  try:
    main(sys.argv[1:])
  except GenericError as e:
    eprint(e)
    sys.exit(1)
