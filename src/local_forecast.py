#!/usr/bin/env python3
"""Fetch and display the 7-day local forecast from MetService."""

import urllib.request
import json
import argparse
from datetime import datetime

URL = "https://www.metservice.com/publicData/localForecast{city_name}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.metservice.com/",
}


def fetch_forecast(city: str = "Wellington") -> dict:
    req = urllib.request.Request(URL.format(city_name=city), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def get_structured_forecast(data) -> list:
    """Extract structured forecast for next 2 days (excluding today).
    
    Returns a list of dicts with: date, dow, min, max, morning/afternoon/evening forecastWord
    """
    days = data.get("days", [])[1:3]  # Skip today (index 0), get next 2 days
    structured = []
    
    for day in days:
        part_day = day.get("partDayData", {})
        forecast_item = {
            "date": day.get("date"),
            "dow": day.get("dow"),
            "min": day.get("min"),
            "max": day.get("max"),
            "morning": part_day.get("morning", {}).get("forecastWord", ""),
            "afternoon": part_day.get("afternoon", {}).get("forecastWord", ""),
            "evening": part_day.get("evening", {}).get("forecastWord", ""),
        }
        structured.append(forecast_item)
    
    return structured


def display_forecast(data):
    days = data.get("days", [])[:7]
    print(f"{data.get('location', 'Wellington')} 7-Day Forecast".center(60))
    print("=" * 60)
    for day in days:
        dow = day.get("dow", "")
        date = day.get("date", "")
        lo = day.get("min", "?")
        hi = day.get("max", "?")
        summary = day.get("forecastWord", "")
        detail = day.get("forecast", "")
        print(f"\n{dow} {date}  |  {lo}°C – {hi}°C  |  {summary}")
        print(f"  {detail}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch and save local forecasts for multiple cities"
    )
    parser.add_argument(
        "--city",
        nargs="+",
        default=["Wellington"],
        help="City names to fetch forecasts for (default: Wellington)",
    )
    parser.add_argument(
        "--output",
        default="localForecast.json",
        help="Output JSON file path (default: localForecast.json)",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="Display traditional forecast format for each city",
    )
    
    args = parser.parse_args()
    
    # Fetch forecasts for all cities
    all_forecasts = {
        "generated_at": datetime.now().isoformat(),
        "forecasts": {},
    }
    
    for city in args.city:
        try:
            data = fetch_forecast(city)
            structured = get_structured_forecast(data)
            all_forecasts["forecasts"][city] = structured
            
            if args.display:
                print(f"\n{'='*60}")
                print(f"Forecast for {city}")
                print('='*60)
                display_forecast(data)
        except Exception as e:
            print(f"Error fetching forecast for {city}: {e}")
            all_forecasts["forecasts"][city] = None
    
    # Save to JSON file
    with open(args.output, "w") as f:
        json.dump(all_forecasts, f, indent=2)
    
    print(f"\nForecasts saved to {args.output}")
    print(json.dumps(all_forecasts, indent=2))
