export function EmptyGarden() {
  return (
    <div className="empty-garden" data-testid="empty-garden">
      <div className="empty-garden__icon">🌱</div>
      <h2 className="empty-garden__title">Your garden is empty</h2>
      <p className="empty-garden__text">
        Paste a diff or notes above to grow your first knowledge plant!
      </p>
    </div>
  );
}
