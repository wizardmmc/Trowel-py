import { motion } from "framer-motion";
import type { SessionStats } from "../../api/client";

interface ReviewCompletionProps {
  stats: SessionStats;
  onBackToGarden: () => void;
}

export function ReviewCompletion({ stats, onBackToGarden }: ReviewCompletionProps) {
  return (
    <motion.div
      className="review-completion"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <h2 className="review-completion__title" data-testid="completion-title">
        复习完成
      </h2>

      <div className="review-completion__stats">
        <div className="review-completion__stat">
          <span className="review-completion__stat-value" data-testid="stat-total">
            {stats.total}
          </span>
          <span className="review-completion__stat-label">已复习卡片</span>
        </div>

        <div className="review-completion__stat">
          <span className="review-completion__stat-value" data-testid="stat-accuracy">
            {stats.accuracy.toFixed(1)}%
          </span>
          <span className="review-completion__stat-label">准确率</span>
        </div>

        <div className="review-completion__stat">
          <span className="review-completion__stat-value" data-testid="stat-avg-rating">
            {stats.avg_rating.toFixed(1)}
          </span>
          <span className="review-completion__stat-label">平均评分</span>
        </div>
      </div>

      <button
        className="review-completion__back-btn"
        data-testid="back-to-garden"
        onClick={onBackToGarden}
      >
        返回花园
      </button>
    </motion.div>
  );
}
