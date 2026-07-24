import type { Variants } from "framer-motion";

export type PetBehavior = "idle" | "wander" | "nap" | "lookAtPlant";

export const petVariants: Variants = {
  idle: {
    y: [0, -8, 0],
    transition: {
      y: { duration: 2, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" },
    },
  },
  wander: {
    y: [0, -6, 0],
    x: [0, -20, -10, -25, 0],
    transition: {
      y: { duration: 2, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" },
      x: { duration: 8, ease: "easeInOut" },
    },
  },
  nap: {
    scale: 0.9,
    opacity: 0.6,
    rotate: 5,
    transition: { duration: 0.5, ease: "easeInOut" },
  },
  lookAtPlant: {
    rotate: -10,
    x: -30,
    transition: { duration: 0.8, ease: "easeOut" },
  },
};

export const speechBubbleVariants: Variants = {
  hidden: { opacity: 0, y: 10, scale: 0.8 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.3, ease: "easeOut" },
  },
  exit: {
    opacity: 0,
    y: -10,
    scale: 0.8,
    transition: { duration: 0.2, ease: "easeIn" },
  },
};
