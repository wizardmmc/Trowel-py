from trowel_py.events.types import EventType
from trowel_py.events.handlers.types import EventHandler
from .feynman import FeynmanHandler
from .discovery import DiscoveryHandler
from .gift import GiftHandler
from .sign_in import SignInHandler
from .challenge import ChallengeHandler
from .story import StoryHandler
from .growth import GrowthHandler

HANDLERS: dict[EventType, EventHandler] = {
    "feynman": FeynmanHandler(),
    "discovery": DiscoveryHandler(),
    "gift": GiftHandler(),
    "sign_in": SignInHandler(),
    "challenge": ChallengeHandler(),
    "story": StoryHandler(),
    "growth": GrowthHandler()
}