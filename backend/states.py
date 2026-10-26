"""The authoritative ride state machine.

    REQUESTED -> OFFERED -> ACCEPTED -> ARRIVED -> IN_PROGRESS -> COMPLETED

    REQUESTED   searching; no driver currently holds an offer
    OFFERED     exactly one driver holds a pending, time-limited offer
    ACCEPTED    a driver is assigned and heading to the pickup
    ARRIVED     the driver is waiting at the pickup point
    IN_PROGRESS the passenger is on board
    COMPLETED   terminal: trip finished, fare recorded
    CANCELLED   terminal: cancelled by passenger, driver (after pickup wait) or admin
    NO_DRIVER_AVAILABLE terminal: the search window closed without an acceptance

Offer outcomes (rejected / expired) live on ride_offers and move the ride from
OFFERED back to REQUESTED so dispatch can try the next candidate. A driver who
cancels before pickup also returns the ride to REQUESTED (reassignment).
"""
from enum import Enum


class RideStatus(str, Enum):
    REQUESTED = "REQUESTED"
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    ARRIVED = "ARRIVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_DRIVER_AVAILABLE = "NO_DRIVER_AVAILABLE"


class OfferStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


S = RideStatus

TRANSITIONS = {
    S.REQUESTED: {S.OFFERED, S.CANCELLED, S.NO_DRIVER_AVAILABLE},
    S.OFFERED: {S.ACCEPTED, S.REQUESTED, S.CANCELLED, S.NO_DRIVER_AVAILABLE},
    S.ACCEPTED: {S.ARRIVED, S.REQUESTED, S.CANCELLED},
    S.ARRIVED: {S.IN_PROGRESS, S.CANCELLED},
    S.IN_PROGRESS: {S.COMPLETED},
    S.COMPLETED: set(),
    S.CANCELLED: set(),
    S.NO_DRIVER_AVAILABLE: set(),
}

SEARCHING = frozenset({S.REQUESTED, S.OFFERED})
DRIVER_ASSIGNED = frozenset({S.ACCEPTED, S.ARRIVED, S.IN_PROGRESS})
ACTIVE = SEARCHING | DRIVER_ASSIGNED
TERMINAL = frozenset({S.COMPLETED, S.CANCELLED, S.NO_DRIVER_AVAILABLE})
PASSENGER_CANCELLABLE = frozenset({S.REQUESTED, S.OFFERED, S.ACCEPTED, S.ARRIVED})


def can_transition(current: RideStatus, target: RideStatus) -> bool:
    return RideStatus(target) in TRANSITIONS[RideStatus(current)]


def values(statuses) -> list:
    return [s.value for s in statuses]
