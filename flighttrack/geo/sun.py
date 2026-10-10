"""Where the sun is, for day and night shading.

A low-precision solar position (good to a fraction of a degree), which is far more than the
shading needs.
"""

from math import asin, atan2, cos, degrees, radians, sin

J2000_UNIX = 946728000.0  # 2000-01-01 12:00 UTC
# Civil twilight: the sky is fully dark by 6 degrees below the horizon, fully light 6 above.
TWILIGHT_DEG = 6.0


def sun_elevation_deg(lat: float, lon: float, unix_time: float) -> float:
    """Angle of the sun above the horizon (negative when below) at a place and time."""
    days = (unix_time - J2000_UNIX) / 86400.0
    mean_longitude = radians((280.460 + 0.9856474 * days) % 360.0)
    mean_anomaly = radians((357.528 + 0.9856003 * days) % 360.0)
    ecliptic_longitude = (
        mean_longitude + radians(1.915) * sin(mean_anomaly) + radians(0.020) * sin(2 * mean_anomaly)
    )
    obliquity = radians(23.439 - 0.0000004 * days)
    declination = asin(sin(obliquity) * sin(ecliptic_longitude))
    right_ascension = atan2(cos(obliquity) * sin(ecliptic_longitude), cos(ecliptic_longitude))
    sidereal_deg = (18.697374558 + 24.06570982441908 * days) % 24.0 * 15.0
    hour_angle = radians(sidereal_deg + lon) - right_ascension
    phi = radians(lat)
    return degrees(
        asin(sin(phi) * sin(declination) + cos(phi) * cos(declination) * cos(hour_angle))
    )


def daylight(lat: float, lon: float, unix_time: float) -> float:
    """0.0 in full night, 1.0 in full day, in between through twilight."""
    elevation = sun_elevation_deg(lat, lon, unix_time)
    return min(max((elevation + TWILIGHT_DEG) / (2 * TWILIGHT_DEG), 0.0), 1.0)
