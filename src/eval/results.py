"""Evaluation result models.

Defines data structures for storing and serializing evaluation outcomes,
including timing metrics, cart contents, and success indicators.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    """Represents an item found in the shopping cart."""

    name: str = Field(description="Product name as displayed in cart")
    quantity: int = Field(default=1, description="Quantity in cart")
    price: str | None = Field(default=None, description="Price as displayed (e.g., '$4.99')")
    raw_text: str | None = Field(default=None, description="Raw text extracted from cart")

    def matches(self, target: str, fuzzy: bool = True) -> bool:
        """Check if this cart item matches a target item string.

        Args:
            target: Target item to match (e.g., 'apples', '2 liters milk')
            fuzzy: Use fuzzy matching (contains) vs exact match

        Returns:
            True if this item matches the target
        """
        name_lower = self.name.lower()
        target_lower = target.lower()

        # Extract just the product name from target (remove quantity prefixes)
        # "6 apples" -> "apples", "2 liters milk" -> "milk"
        target_words = target_lower.split()
        product_words = [w for w in target_words if not w.replace(".", "").isdigit() and w not in ("liters", "liter", "kg", "g", "lb", "oz", "ml", "l")]

        if fuzzy:
            # Check if any product word is in the cart item name
            return any(word in name_lower for word in product_words)
        else:
            return target_lower in name_lower or name_lower in target_lower


class RunMetrics(BaseModel):
    """Timing and performance metrics for an evaluation run."""

    start_time: datetime = Field(description="When the run started")
    end_time: datetime | None = Field(default=None, description="When the run ended")
    total_duration_seconds: float | None = Field(default=None, description="Total run duration")

    # Per-item timings
    item_durations: dict[str, float] = Field(
        default_factory=dict,
        description="Duration in seconds for each item (item -> seconds)",
    )

    # Agent step counts
    steps_per_item: dict[str, int] = Field(
        default_factory=dict,
        description="Number of agent steps taken for each item",
    )

    # Cart verification timing
    cart_check_duration_seconds: float | None = Field(
        default=None,
        description="Time taken to verify cart contents",
    )

    def finalize(self, end_time: datetime | None = None) -> None:
        """Finalize metrics with end time and calculate totals."""
        self.end_time = end_time or datetime.now()
        self.total_duration_seconds = (self.end_time - self.start_time).total_seconds()

    @property
    def avg_item_duration(self) -> float | None:
        """Average duration per item in seconds."""
        if not self.item_durations:
            return None
        return sum(self.item_durations.values()) / len(self.item_durations)

    @property
    def avg_steps_per_item(self) -> float | None:
        """Average agent steps per item."""
        if not self.steps_per_item:
            return None
        return sum(self.steps_per_item.values()) / len(self.steps_per_item)


class ItemResult(BaseModel):
    """Result for a single item addition attempt."""

    item: str = Field(description="The item that was requested")
    status: Literal["success", "failed", "uncertain", "timeout", "error"] = Field(
        description="Result status"
    )
    duration_seconds: float = Field(description="Time taken for this item")
    steps_taken: int = Field(default=0, description="Number of agent steps")
    success_evidence: str | None = Field(
        default=None, description="Evidence of success from agent history"
    )
    error_message: str | None = Field(default=None, description="Error message if failed")
    matched_cart_item: CartItem | None = Field(
        default=None, description="Cart item that matched this request"
    )


class EvalResult(BaseModel):
    """Complete result for an evaluation run."""

    run_name: str = Field(description="Name of the evaluation run")
    config_summary: dict = Field(
        default_factory=dict,
        description="Summary of configuration used",
    )

    # Overall status
    status: Literal["success", "partial", "failed", "error"] = Field(
        default="error",
        description="Overall run status",
    )
    success_rate: float = Field(
        default=0.0,
        description="Percentage of items successfully added (0.0 - 1.0)",
    )

    # Item-level results
    items_requested: list[str] = Field(default_factory=list, description="Items that were requested")
    item_results: list[ItemResult] = Field(default_factory=list, description="Per-item results")

    # Cart verification
    cart_items: list[CartItem] = Field(default_factory=list, description="Items found in cart")
    cart_verified: bool = Field(default=False, description="Whether cart was successfully verified")
    cart_raw_content: str | None = Field(default=None, description="Raw cart content extracted")

    # Timing metrics
    metrics: RunMetrics = Field(default_factory=lambda: RunMetrics(start_time=datetime.now()))

    # Metadata
    timestamp: datetime = Field(default_factory=datetime.now)
    profile_dir: str | None = Field(default=None, description="Temp profile directory used")
    error: str | None = Field(default=None, description="Error message if run failed")

    def calculate_success_rate(self) -> float:
        """Calculate and update the success rate based on item results."""
        if not self.item_results:
            self.success_rate = 0.0
        else:
            successes = sum(1 for r in self.item_results if r.status == "success")
            self.success_rate = successes / len(self.item_results)

        # Update overall status
        if self.success_rate == 1.0:
            self.status = "success"
        elif self.success_rate > 0:
            self.status = "partial"
        else:
            self.status = "failed"

        return self.success_rate

    def get_summary(self) -> str:
        """Get a human-readable summary of the evaluation result."""
        lines = [
            f"Evaluation: {self.run_name}",
            f"Status: {self.status.upper()}",
            f"Success Rate: {self.success_rate:.1%}",
            "",
            "Items:",
        ]

        for result in self.item_results:
            status_icon = {
                "success": "[+]",
                "failed": "[-]",
                "uncertain": "[?]",
                "timeout": "[T]",
                "error": "[!]",
            }.get(result.status, "[?]")
            lines.append(f"  {status_icon} {result.item} ({result.duration_seconds:.1f}s, {result.steps_taken} steps)")
            if result.matched_cart_item:
                lines.append(f"      -> Found: {result.matched_cart_item.name}")

        lines.append("")
        lines.append("Cart Contents:")
        if self.cart_items:
            for item in self.cart_items:
                price_str = f" - {item.price}" if item.price else ""
                lines.append(f"  - {item.quantity}x {item.name}{price_str}")
        else:
            lines.append("  (empty or not verified)")

        if self.metrics.total_duration_seconds:
            lines.append("")
            lines.append(f"Total Duration: {self.metrics.total_duration_seconds:.1f}s")
            if self.metrics.avg_item_duration:
                lines.append(f"Avg per Item: {self.metrics.avg_item_duration:.1f}s")

        return "\n".join(lines)

    def to_file(self, path: str | Path) -> None:
        """Save result to a JSON file.

        Args:
            path: Path to save the result file
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)

    @classmethod
    def from_file(cls, path: str | Path) -> EvalResult:
        """Load result from a JSON file.

        Args:
            path: Path to the result file

        Returns:
            EvalResult instance
        """
        with open(path) as f:
            data = json.load(f)
        return cls.model_validate(data)


class EvalSession(BaseModel):
    """Results from a complete evaluation session with multiple runs."""

    name: str = Field(description="Session name")
    results: list[EvalResult] = Field(default_factory=list)
    start_time: datetime = Field(default_factory=datetime.now)
    end_time: datetime | None = Field(default=None)

    def add_result(self, result: EvalResult) -> None:
        """Add a result to the session."""
        self.results.append(result)

    def finalize(self) -> None:
        """Mark the session as complete."""
        self.end_time = datetime.now()

    @property
    def total_duration_seconds(self) -> float | None:
        """Total session duration in seconds."""
        if not self.end_time:
            return None
        return (self.end_time - self.start_time).total_seconds()

    @property
    def overall_success_rate(self) -> float:
        """Average success rate across all runs."""
        if not self.results:
            return 0.0
        return sum(r.success_rate for r in self.results) / len(self.results)

    def get_summary(self) -> str:
        """Get a summary of the entire session."""
        lines = [
            f"Evaluation Session: {self.name}",
            f"Runs: {len(self.results)}",
            f"Overall Success Rate: {self.overall_success_rate:.1%}",
            "",
        ]

        for result in self.results:
            lines.append(f"  [{result.status.upper()}] {result.run_name}: {result.success_rate:.1%}")

        if self.total_duration_seconds:
            lines.append("")
            lines.append(f"Total Duration: {self.total_duration_seconds:.1f}s")

        return "\n".join(lines)

    def to_file(self, path: str | Path) -> None:
        """Save session to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)
