# Green Light Cal

Green Light Cinema Showtimes to Google Calendar

## Description

This python project parses the showtimes page for Green Light Cinema and
updates a Google Calendar calendar. No need to run this yourself if you
want this:

 - [View Calendar](https://calendar.google.com/calendar/embed?src=1c6920e0ec3639f7c1ebbbff43ecd389e97739e6d8711d0eaf06cfb455d1e0c4%40group.calendar.google.com&ctz=America%2FNew_York)
 - [Subscribe to Calendar](https://calendar.google.com/calendar/u/0?cid=MWM2OTIwZTBlYzM2MzlmN2MxZWJiYmZmNDNlY2QzODllOTc3MzllNmQ4NzExZDBlYWYwNmNmYjQ1NWQxZTBjNEBncm91cC5jYWxlbmRhci5nb29nbGUuY29t)
 - [iCal Link](https://calendar.google.com/calendar/ical/1c6920e0ec3639f7c1ebbbff43ecd389e97739e6d8711d0eaf06cfb455d1e0c4%40group.calendar.google.com/public/basic.ics)

The code specific to Green Light Cinema is located exclusively in `main.py`
while `google_cal.py` has all of the Google Calendar operations hidden behind
a simple interface.

## Setup

Standard Python3 project setup (`pip install -r requirements.txt`), but you
will require a Google Service Account with the Calendar role. Save the JSON
credentials file and reference the file via command line flag, environment
variable, or `.env` file (see `--help` for more info).

Supports JSON outputting for caching, exploring, and debugging.

## Docker

Build the image as follows:

```bash
docker build -t greenlight_cal .
```

Then run the container, setting the environment variable `CALENDAR_ID`
to the appropriate Calender ID and setting up the service worker credential
file as `credentials.json`:

```bash
docker run --rm \
    -e="CREDENTIALS_JSON=$(< credentials.json)" \
    -e="CALENDAR_ID=${CALENDAR_ID:??}" \
    greenlight_cal calendar --list
```

This command should output all available calendars the service account has
access to. To create a calender:

```bash
docker run --rm \
    -e="CREDENTIALS_JSON=$(< credentials.json)" \
    greenlight_cal calendar --create "My Calendar"
```

This should spit out a Calendar ID. To give yourself access to that calendar:

```bash
docker run --rm \
    -e="CREDENTIALS_JSON=$(< credentials.json)" \
    -e="CALENDAR_ID=${CALENDAR_ID:??}" \
    greenlight_cal calendar --add_owner me@gmail.com
```

And all that's left is to sync the events:

```bash
docker run --rm \
    -e="CREDENTIALS_JSON=$(< credentials.json)" \
    -e="CALENDAR_ID=${CALENDAR_ID:??}" \
    greenlight_cal events --update
```


