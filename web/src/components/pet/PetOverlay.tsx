import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { usePetStore } from "../../stores/petStore";
import { useEventStore } from "../../stores/eventStore";
import { PetSVG } from "./PetSVG";
import {
  petVariants,
  speechBubbleVariants,
  type PetBehavior,
} from "./petAnimations";
import "./PetOverlay.css";

const BEHAVIORS: readonly PetBehavior[] = ["idle", "wander", "nap", "lookAtPlant"];

/** How long the interaction speech bubble stays visible (ms). */
const BUBBLE_DISMISS_MS = 3_000;

interface PetOverlayProps {
  /**
   * Optional click handler. In slice 014 the garden passes nothing, so a click
   * falls back to interact() and the pet says a line. Slice 015 will wire this
   * to open the PetPanel.
   */
  readonly onClick?: () => void;
}

export function PetOverlay({ onClick }: PetOverlayProps) {
  const [behavior, setBehavior] = useState<PetBehavior>("idle");
  const [showBubble, setShowBubble] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pet = usePetStore((s) => s.pet);
  const lastResponse = usePetStore((s) => s.lastResponse);
  const fetchPet = usePetStore((s) => s.fetchPet);
  const interact = usePetStore((s) => s.interact);
  // When an event modal is open, the pet reacts with an excited face — a local
  // UI cue only, no server push involved.
  const currentEvent = useEventStore((s) => s.currentEvent);

  // Load the pet on mount
  useEffect(() => {
    fetchPet();
  }, [fetchPet]);

  // Background behavior loop: every 25-35s pick a random behavior and reschedule.
  useEffect(() => {
    function scheduleNext(): void {
      const delay = 25_000 + Math.random() * 10_000;
      timerRef.current = setTimeout(() => {
        const next = BEHAVIORS[Math.floor(Math.random() * BEHAVIORS.length)];
        setBehavior(next);
        scheduleNext();
      }, delay);
    }
    scheduleNext();
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  // Show the speech bubble whenever a new response arrives, auto-hide after 3s.
  useEffect(() => {
    if (lastResponse) {
      setShowBubble(true);
      const timer = setTimeout(() => setShowBubble(false), BUBBLE_DISMISS_MS);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [lastResponse]);

  const handleClick = useCallback(() => {
    if (onClick) {
      onClick();
    } else {
      void interact();
    }
  }, [onClick, interact]);

  if (!pet) return null;

  return (
    <motion.div
      className="pet-overlay"
      data-testid="pet-overlay"
      variants={petVariants}
      animate={behavior}
      initial="idle"
      onClick={handleClick}
      role="button"
      tabIndex={0}
      aria-label="小锤 — your learning pet"
    >
      <AnimatePresence>
        {showBubble && lastResponse && (
          <motion.div
            className="pet-overlay__speech-bubble"
            variants={speechBubbleVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            // swallow the click so tapping the bubble doesn't re-trigger interact;
            // slice 015 will open PetChat here.
            onClick={(e) => e.stopPropagation()}
          >
            {lastResponse.text}
          </motion.div>
        )}
      </AnimatePresence>
      <div className="pet-overlay__svg">
        <PetSVG
          mood={currentEvent ? "excited" : pet.mood}
          equippedHat={pet.equipped_hat ?? undefined}
        />
      </div>
    </motion.div>
  );
}
