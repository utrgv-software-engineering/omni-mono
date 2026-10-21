"""Example booking tools for the eval_with_environment demo."""

from typing import Any, Dict, Optional

from omniagents.core.tools import function_tool


@function_tool
def create_booking(
    name: str,
    email: str,
    date: str,
    time: str,
    duration_hours: int = 1,
    room_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new booking.

    Args:
        name: Name of the person making the booking
        email: Email address for confirmation
        date: Date for the booking (YYYY-MM-DD format)
        time: Start time for the booking (HH:MM format, 24-hour)
        duration_hours: Duration in hours (default: 1)
        room_type: Type of room (e.g., "conference", "meeting", "office")
        notes: Optional notes for the booking

    Returns:
        Dict with booking confirmation details
    """
    # In a real implementation, this would call an API
    # For demo purposes, we return a mock response
    return {
        "success": True,
        "booking_id": "BK-" + date.replace("-", "") + "-001",
        "details": {
            "name": name,
            "email": email,
            "date": date,
            "time": time,
            "duration_hours": duration_hours,
            "room_type": room_type or "meeting",
            "status": "confirmed",
        },
        "message": f"Booking confirmed for {name} on {date} at {time}",
    }


@function_tool
def get_booking(booking_id: str) -> Dict[str, Any]:
    """Look up an existing booking by ID.

    Args:
        booking_id: The booking ID to look up

    Returns:
        Dict with booking details or error
    """
    # Mock implementation
    if booking_id == "ABC123":
        return {
            "success": True,
            "booking": {
                "id": "ABC123",
                "name": "John Doe",
                "email": "john@example.com",
                "date": "2024-01-15",
                "time": "10:00",
                "duration_hours": 2,
                "room_type": "conference",
                "status": "confirmed",
            },
        }
    else:
        return {
            "success": False,
            "error": f"Booking {booking_id} not found",
        }


@function_tool
def list_available_slots(
    date: str,
    room_type: Optional[str] = None,
) -> Dict[str, Any]:
    """List available time slots for a given date.

    Args:
        date: Date to check (YYYY-MM-DD format)
        room_type: Optional filter by room type

    Returns:
        Dict with available slots
    """
    # Mock implementation
    return {
        "success": True,
        "date": date,
        "available_slots": [
            {"time": "09:00", "duration_available": 2, "room_type": "meeting"},
            {"time": "11:00", "duration_available": 1, "room_type": "meeting"},
            {"time": "14:00", "duration_available": 3, "room_type": "conference"},
            {"time": "16:00", "duration_available": 2, "room_type": "meeting"},
        ],
    }
