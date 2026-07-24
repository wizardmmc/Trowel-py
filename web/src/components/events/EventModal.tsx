import { motion, AnimatePresence } from "framer-motion";
import type { EventLog } from "../../api/client";
import { EVENT_ICONS, ITEM_CATALOG } from "../pet/itemCatalog";
import "./EventModal.css";

interface EventModalProps {
  readonly event: EventLog | null;
  readonly onClaim: () => void;
}

function formatItemLabel(itemId: string): string {
  const entry = ITEM_CATALOG[itemId];
  return entry ? `${entry.emoji} ${entry.label}` : itemId;
}

export function EventModal({ event, onClaim }: EventModalProps) {
  return (
    <AnimatePresence>
      {event && (
        <motion.div
          className="event-modal-overlay"
          data-testid="event-modal-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={onClaim}
        >
          <motion.div
            className="event-modal"
            initial={{ y: 100, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 100, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="事件通知"
          >
            <div className="event-modal__icon">
              {EVENT_ICONS[event.event_type] ?? "✨"}
            </div>

            <div className="event-modal__description">
              {event.description ?? "发生了一个事件"}
            </div>

            {(event.reward_xp > 0 ||
              event.reward_coin > 0 ||
              event.reward_item_id) && (
              <div className="event-modal__rewards">
                {event.reward_xp > 0 && (
                  <div className="event-modal__reward-item">
                    <span className="event-modal__reward-value">
                      +{event.reward_xp}
                    </span>
                    <span className="event-modal__reward-label">XP</span>
                  </div>
                )}
                {event.reward_coin > 0 && (
                  <div className="event-modal__reward-item">
                    <span className="event-modal__reward-value">
                      +{event.reward_coin}
                    </span>
                    <span className="event-modal__reward-label">🪙</span>
                  </div>
                )}
                {event.reward_item_id && (
                  <div className="event-modal__reward-item">
                    <span className="event-modal__reward-value">
                      {formatItemLabel(event.reward_item_id)}
                    </span>
                    <span className="event-modal__reward-label">🎁</span>
                  </div>
                )}
              </div>
            )}

            <button
              className="event-modal__claim-btn"
              onClick={onClaim}
              type="button"
            >
              🎉 领取奖励
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
