import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { usePetStore } from "../../stores/petStore";
import { usePlayerStore } from "../../stores/playerStore";
import { fetchEventHistory, type EventLog } from "../../api/client";
import { PetSVG } from "./PetSVG";
import {
  ITEM_CATALOG,
  FOOD_ITEMS,
  MOOD_LABELS,
  EVENT_TYPE_LABELS,
} from "./itemCatalog";
import "./PetPanel.css";

interface PetPanelProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** element that opened the panel; focus returns here on close (a11y) */
  readonly triggerElement?: HTMLElement | null;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = date.getHours().toString().padStart(2, "0");
  const minute = date.getMinutes().toString().padStart(2, "0");
  return `${month}/${day} ${hour}:${minute}`;
}

export function PetPanel({ open, onClose, triggerElement }: PetPanelProps) {
  const pet = usePetStore((s) => s.pet);
  const petLoading = usePetStore((s) => s.loading);
  const feed = usePetStore((s) => s.feed);
  const interact = usePetStore((s) => s.interact);
  const equipHat = usePetStore((s) => s.equipHat);
  const lastResponse = usePetStore((s) => s.lastResponse);

  const player = usePlayerStore((s) => s.player);
  const inventory = usePlayerStore((s) => s.inventory);
  const playerLoading = usePlayerStore((s) => s.loading);
  const fetchProfile = usePlayerStore((s) => s.fetchProfile);
  const fetchInventory = usePlayerStore((s) => s.fetchInventory);
  const buyItem = usePlayerStore((s) => s.buyItem);

  const [events, setEvents] = useState<readonly EventLog[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);

  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(false);

  // remember the trigger element so we can return focus to it on close
  useEffect(() => {
    if (open && triggerElement) {
      triggerRef.current = triggerElement;
    }
  }, [open, triggerElement]);

  // load everything the panel needs when it opens
  useEffect(() => {
    if (!open) return;
    fetchProfile();
    fetchInventory();
    interact();
    fetchEventHistory(5)
      .then((logs) => setEvents(logs))
      .catch(() => setEvents([]));
    setActionError(null);
  }, [open, fetchProfile, fetchInventory, interact]);

  // move focus to the close button once the slide-in finishes
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => {
      closeRef.current?.focus();
    }, 350);
    return () => clearTimeout(timer);
  }, [open]);

  // ESC to close + a simple Tab focus trap inside the panel
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab" && panelRef.current) {
        const focusable = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  // return focus to the trigger once the panel has closed
  useEffect(() => {
    if (open) return;
    triggerRef.current?.focus();
    triggerRef.current = null;
  }, [open]);

  const isLoading = petLoading || playerLoading;

  // the pet stores the worn hat as an inventory ROW id; resolve it to the
  // catalog id (hat_straw ...) that PetSVG and the status display need.
  const equippedRow = pet?.equipped_hat
    ? inventory.find((i) => i.id === pet.equipped_hat)
    : null;
  const equippedCatalogId = equippedRow?.item_id;
  const equippedCatalog = equippedCatalogId
    ? ITEM_CATALOG[equippedCatalogId]
    : null;

  const moodInfo = pet
    ? MOOD_LABELS[pet.mood] ?? MOOD_LABELS.normal
    : MOOD_LABELS.normal;

  const hatItems = inventory.filter((i) => i.item_type === "hat");

  const handleFeed = useCallback(
    async (catalogId: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setActionError(null);
      try {
        // feed/equip take a ROW id, so resolve the catalog id to an owned row.
        // read the latest inventory from the store via getState — the closure
        // `inventory` is a stale snapshot once we've awaited anything.
        const findRow = () =>
          usePlayerStore.getState().inventory.find(
            (i) => i.item_id === catalogId && i.item_type === "food",
          );

        let row = findRow();
        if (!row) {
          // not owned: buy deducts coins server-side and the store re-fetches
          // the inventory; then we resolve the freshly granted row.
          await buyItem(catalogId);
          row = findRow();
        }
        if (!row) {
          setActionError("购买成功但未找到食物，请重试");
          return;
        }
        await feed(row.id);
        await fetchInventory();
      } catch (err) {
        const message = err instanceof Error ? err.message : "喂食失败";
        setActionError(message);
      } finally {
        busyRef.current = false;
      }
    },
    [feed, buyItem, fetchInventory],
  );

  const handleEquipHat = useCallback(
    async (rowId: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setActionError(null);
      try {
        await equipHat(rowId);
      } catch (err) {
        const message = err instanceof Error ? err.message : "装备失败";
        setActionError(message);
      } finally {
        busyRef.current = false;
      }
    },
    [equipHat],
  );

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="pet-panel-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          onClick={onClose}
        >
          <motion.div
            ref={panelRef}
            className="pet-panel"
            role="dialog"
            aria-label="宠物面板"
            aria-modal="true"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ duration: 0.3, ease: "easeOut" }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="pet-panel__header">
              <h2 className="pet-panel__title">小锤的花园小屋</h2>
              <button
                ref={closeRef}
                className="pet-panel__close"
                onClick={onClose}
                aria-label="关闭面板"
              >
                {"✕"}
              </button>
            </div>

            {/* Pet display */}
            <div className="pet-panel__pet-area">
              <div className="pet-panel__pet-svg">
                <PetSVG
                  mood={pet?.mood ?? "normal"}
                  equippedHat={equippedCatalogId}
                />
              </div>
              {lastResponse && (
                <div className="pet-panel__speech-bubble">
                  {lastResponse.text}
                </div>
              )}
            </div>

            {/* Status */}
            <div className="pet-panel__section">
              <h3 className="pet-panel__section-title">状态</h3>
              <div className="pet-panel__status-list">
                <div className="pet-panel__status-item">
                  <span className="pet-panel__status-label">心情</span>
                  <span>{`${moodInfo.emoji} ${moodInfo.text}`}</span>
                </div>
                <div className="pet-panel__status-item">
                  <span className="pet-panel__status-label">饱腹度</span>
                  <div className="pet-panel__hunger-bar">
                    <div
                      className="pet-panel__hunger-fill"
                      style={{ width: `${pet?.hunger ?? 0}%` }}
                    />
                  </div>
                  <span className="pet-panel__hunger-value">{`${pet?.hunger ?? 0}%`}</span>
                </div>
                <div className="pet-panel__status-item">
                  <span className="pet-panel__status-label">装备</span>
                  <span>
                    {equippedCatalog
                      ? `${equippedCatalog.emoji} ${equippedCatalog.label}`
                      : "无"}
                  </span>
                </div>
              </div>
            </div>

            {/* Feed */}
            <div className="pet-panel__section">
              <h3 className="pet-panel__section-title">喂食</h3>
              <div className="pet-panel__food-list">
                {FOOD_ITEMS.map(([itemId, entry]) => {
                  const ownedCount = inventory.filter(
                    (i) => i.item_id === itemId && i.item_type === "food",
                  ).length;
                  const canAfford = (player?.coins ?? 0) >= entry.price;
                  const canFeed = ownedCount > 0 || canAfford;
                  return (
                    <button
                      key={itemId}
                      className="pet-panel__food-btn"
                      disabled={isLoading || !canFeed}
                      onClick={() => handleFeed(itemId)}
                    >
                      <span className="pet-panel__food-info">
                        <span>{entry.emoji}</span>
                        <span>{entry.label}</span>
                        {ownedCount > 0 && (
                          <span className="pet-panel__food-owned">
                            {`x${ownedCount}`}
                          </span>
                        )}
                      </span>
                      <span className="pet-panel__food-price">
                        {ownedCount > 0 ? "背包" : `${entry.price} 🪙`}
                      </span>
                    </button>
                  );
                })}
              </div>
              {actionError && (
                <div className="pet-panel__error">{actionError}</div>
              )}
            </div>

            {/* Hat inventory */}
            <div className="pet-panel__section">
              <h3 className="pet-panel__section-title">背包 - 帽子</h3>
              {hatItems.length === 0 ? (
                <div className="pet-panel__empty">还没有帽子，继续探索吧！</div>
              ) : (
                <div className="pet-panel__hat-list">
                  {hatItems.map((item) => {
                    const catalog = ITEM_CATALOG[item.item_id];
                    const isEquipped = pet?.equipped_hat === item.id;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className={`pet-panel__hat-item${isEquipped ? " pet-panel__hat-item--equipped" : ""}`}
                        onClick={() => handleEquipHat(item.id)}
                      >
                        <span className="pet-panel__hat-info">
                          <span>{catalog?.emoji ?? "?"}</span>
                          <span>{catalog?.label ?? item.item_id}</span>
                        </span>
                        {isEquipped && (
                          <span className="pet-panel__hat-check">✓</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Event log */}
            <div className="pet-panel__section">
              <h3 className="pet-panel__section-title">最近事件</h3>
              {events.length === 0 ? (
                <div className="pet-panel__empty">暂无事件记录</div>
              ) : (
                <div className="pet-panel__event-list">
                  {events.map((event) => (
                    <div key={event.id} className="pet-panel__event-item">
                      <span>
                        <span className="pet-panel__event-type">
                          {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
                        </span>
                        <span className="pet-panel__event-time">
                          {formatTime(event.triggered_at)}
                        </span>
                      </span>
                      <span className="pet-panel__event-reward">
                        {event.reward_xp > 0 && `+${event.reward_xp}XP`}
                        {event.reward_coin > 0 && ` +${event.reward_coin}🪙`}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
