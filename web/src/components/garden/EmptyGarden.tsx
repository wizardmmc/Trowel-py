export function EmptyGarden() {
  return (
    <div className="empty-garden" data-testid="empty-garden">
      <div className="empty-garden__icon" aria-hidden="true">
        <svg className="empty-garden__svg" viewBox="0 0 24 24">
          <path d="M12 21v-8" />
          <path d="M12 13c0-4-3-6-7-6 0 4 3 6 7 6z" />
          <path d="M12 11c0-3 2.5-5 6-5 0 3-2.5 5-6 5z" />
        </svg>
      </div>
      <h2 className="empty-garden__title">花园还是空的</h2>
      <p className="empty-garden__text">
        在左侧「提取」里粘贴一段 diff 或笔记，种下你的第一株知识植物。
      </p>
    </div>
  );
}
