import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { fetchEventHistory, type EventLog } from "../../api/client";
import { usePetStore } from "../../stores/petStore";
import { usePlayerStore } from "../../stores/playerStore";
import { PetPanelContent } from "./PetPanelContent";
import "./PetPanel.css";

interface PetPanelProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly triggerElement?: HTMLElement | null;
}

export function PetPanel({ open, onClose, triggerElement }: PetPanelProps) {
  const pet = usePetStore((state) => state.pet);
  const petLoading = usePetStore((state) => state.loading);
  const feed = usePetStore((state) => state.feed);
  const interact = usePetStore((state) => state.interact);
  const equipHat = usePetStore((state) => state.equipHat);
  const lastResponse = usePetStore((state) => state.lastResponse);

  const player = usePlayerStore((state) => state.player);
  const inventory = usePlayerStore((state) => state.inventory);
  const playerLoading = usePlayerStore((state) => state.loading);
  const fetchProfile = usePlayerStore((state) => state.fetchProfile);
  const fetchInventory = usePlayerStore((state) => state.fetchInventory);
  const buyItem = usePlayerStore((state) => state.buyItem);

  const [events, setEvents] = useState<readonly EventLog[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    if (open && triggerElement) {
      triggerRef.current = triggerElement;
    }
  }, [open, triggerElement]);

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

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => {
      closeRef.current?.focus();
    }, 350);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key === "Tab" && panelRef.current) {
        const focusable = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (open) return;
    triggerRef.current?.focus();
    triggerRef.current = null;
  }, [open]);

  const handleFeed = useCallback(
    async (catalogId: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setActionError(null);
      try {
        // 异步购买后必须从 store 读取最新 row id，不能使用闭包快照。
        const findRow = () =>
          usePlayerStore
            .getState()
            .inventory.find(
              (item) =>
                item.item_id === catalogId && item.item_type === "food",
            );

        let row = findRow();
        if (!row) {
          await buyItem(catalogId);
          row = findRow();
        }
        if (!row) {
          setActionError("购买成功但未找到食物，请重试");
          return;
        }
        await feed(row.id);
        await fetchInventory();
      } catch (error) {
        const message = error instanceof Error ? error.message : "喂食失败";
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
      } catch (error) {
        const message = error instanceof Error ? error.message : "装备失败";
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
            onClick={(event) => event.stopPropagation()}
          >
            <PetPanelContent
              pet={pet}
              lastResponse={lastResponse}
              player={player}
              inventory={inventory}
              events={events}
              actionError={actionError}
              isLoading={petLoading || playerLoading}
              closeRef={closeRef}
              onClose={onClose}
              onFeed={handleFeed}
              onEquipHat={handleEquipHat}
            />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
