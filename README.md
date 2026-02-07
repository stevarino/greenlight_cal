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
