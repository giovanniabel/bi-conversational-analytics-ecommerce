"""
Visualization engine.

Generates Plotly charts from query results based on the
LLM's chart type suggestion and the data shape.
"""

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.logger import get_logger

logger = get_logger(__name__)

# Consistent dark theme for all charts
CHART_THEME = {
    "template": "plotly_dark",
    "color_sequence": [
        "#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd",
        "#818cf8", "#60a5fa", "#38bdf8", "#22d3ee",
        "#34d399", "#4ade80", "#facc15", "#fb923c",
        "#f87171", "#e879f9",
    ],
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font_color": "#e2e8f0",
    "gridcolor": "rgba(148,163,184,0.1)",
}


class Visualizer:
    """
    Creates Plotly visualizations from query result DataFrames.

    Selects the appropriate chart type based on the LLM suggestion
    and the shape/types of the data.
    """

    def create_chart(
        self,
        df: pd.DataFrame,
        chart_type: str,
        question: str = "",
    ) -> Optional[go.Figure]:
        """
        Create a Plotly chart from query results.

        Args:
            df: Query result DataFrame.
            chart_type: Suggested chart type from the LLM.
            question: Original question (used for chart title).

        Returns:
            A Plotly Figure, or None if no chart is appropriate.
        """
        if df.empty or chart_type == "none":
            return None

        if chart_type == "metric" and len(df) == 1 and len(df.columns) <= 2:
            return self._create_metric(df, question)

        try:
            chart_builders = {
                "line": self._create_line_chart,
                "bar": self._create_bar_chart,
                "horizontal_bar": self._create_horizontal_bar_chart,
                "scatter": self._create_scatter_chart,
                "pie": self._create_pie_chart,
                "table": None,  # Tables rendered natively in Streamlit
                "metric": self._create_metric,
            }

            builder = chart_builders.get(chart_type)
            if builder is None:
                return None

            fig = builder(df, question)
            if fig:
                self._apply_theme(fig)
            return fig

        except Exception as e:
            logger.warning("chart_creation_failed", chart_type=chart_type, error=str(e))
            return None

    def _detect_columns(self, df: pd.DataFrame) -> dict:
        """Detect column roles based on types."""
        info = {"categorical": [], "numeric": [], "datetime": []}

        for col in df.columns:
            dtype = df[col].dtype
            if pd.api.types.is_numeric_dtype(dtype):
                info["numeric"].append(col)
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                info["datetime"].append(col)
            else:
                # Check if string column looks like dates
                sample = df[col].dropna().head(5).astype(str)
                try:
                    pd.to_datetime(sample)
                    info["datetime"].append(col)
                except (ValueError, TypeError):
                    info["categorical"].append(col)

        return info

    def _create_line_chart(self, df: pd.DataFrame, question: str) -> Optional[go.Figure]:
        """Create a line chart (best for time series)."""
        cols = self._detect_columns(df)

        x_col = (cols["datetime"] or cols["categorical"] or [df.columns[0]])[0]
        y_cols = cols["numeric"] or [df.columns[-1]]

        if x_col in cols["datetime"] and df[x_col].dtype == object:
            try:
                df = df.copy()
                df[x_col] = pd.to_datetime(df[x_col])
                df = df.sort_values(x_col)
            except (ValueError, TypeError):
                pass

        if len(y_cols) == 1:
            fig = px.line(
                df, x=x_col, y=y_cols[0],
                markers=True,
                color_discrete_sequence=CHART_THEME["color_sequence"],
            )
        else:
            fig = px.line(
                df, x=x_col, y=y_cols[:4],
                markers=True,
                color_discrete_sequence=CHART_THEME["color_sequence"],
            )

        fig.update_traces(line=dict(width=2.5))
        return fig

    def _create_bar_chart(self, df: pd.DataFrame, question: str) -> Optional[go.Figure]:
        """Create a vertical bar chart."""
        cols = self._detect_columns(df)

        x_col = (cols["categorical"] or cols["datetime"] or [df.columns[0]])[0]
        y_col = (cols["numeric"] or [df.columns[-1]])[0]

        fig = px.bar(
            df, x=x_col, y=y_col,
            color_discrete_sequence=CHART_THEME["color_sequence"],
        )
        fig.update_traces(marker_line_width=0)
        return fig

    def _create_horizontal_bar_chart(self, df: pd.DataFrame, question: str) -> Optional[go.Figure]:
        """Create a horizontal bar chart (good for long category names)."""
        cols = self._detect_columns(df)

        y_col = (cols["categorical"] or [df.columns[0]])[0]
        x_col = (cols["numeric"] or [df.columns[-1]])[0]

        # Sort by value for better readability
        df_sorted = df.sort_values(x_col, ascending=True)

        fig = px.bar(
            df_sorted, x=x_col, y=y_col,
            orientation="h",
            color_discrete_sequence=CHART_THEME["color_sequence"],
        )
        fig.update_traces(marker_line_width=0)
        return fig

    def _create_scatter_chart(self, df: pd.DataFrame, question: str) -> Optional[go.Figure]:
        """Create a scatter plot."""
        cols = self._detect_columns(df)

        if len(cols["numeric"]) >= 2:
            x_col = cols["numeric"][0]
            y_col = cols["numeric"][1]
            color_col = cols["categorical"][0] if cols["categorical"] else None

            fig = px.scatter(
                df, x=x_col, y=y_col, color=color_col,
                color_discrete_sequence=CHART_THEME["color_sequence"],
            )
            return fig

        return None

    def _create_pie_chart(self, df: pd.DataFrame, question: str) -> Optional[go.Figure]:
        """Create a pie chart (only when composition is meaningful)."""
        cols = self._detect_columns(df)

        if not cols["categorical"] or not cols["numeric"]:
            return None

        name_col = cols["categorical"][0]
        value_col = cols["numeric"][0]

        # Limit slices for readability
        if len(df) > 8:
            df_sorted = df.sort_values(value_col, ascending=False)
            top = df_sorted.head(7)
            others = pd.DataFrame({
                name_col: ["Others"],
                value_col: [df_sorted.iloc[7:][value_col].sum()],
            })
            df = pd.concat([top, others], ignore_index=True)

        fig = px.pie(
            df, names=name_col, values=value_col,
            color_discrete_sequence=CHART_THEME["color_sequence"],
            hole=0.4,
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        return fig

    def _create_metric(self, df: pd.DataFrame, question: str) -> Optional[go.Figure]:
        """Create a KPI-style metric card."""
        if df.empty:
            return None

        value = df.iloc[0, -1] if len(df.columns) > 1 else df.iloc[0, 0]
        label = df.columns[-1] if len(df.columns) > 1 else df.columns[0]

        # Format the value
        if isinstance(value, (int, float)):
            if abs(value) >= 1_000_000:
                display_value = f"{value / 1_000_000:,.1f}M"
            elif abs(value) >= 1_000:
                display_value = f"{value:,.0f}"
            else:
                display_value = f"{value:,.2f}"
        else:
            display_value = str(value)

        fig = go.Figure()
        fig.add_trace(go.Indicator(
            mode="number",
            value=float(value) if isinstance(value, (int, float)) else 0,
            title={"text": label.replace("_", " ").title(), "font": {"size": 18}},
            number={"font": {"size": 48, "color": "#6366f1"}},
        ))
        fig.update_layout(height=200)

        return fig

    def _apply_theme(self, fig: go.Figure) -> None:
        """Apply consistent dark theme to a chart."""
        fig.update_layout(
            template=CHART_THEME["template"],
            paper_bgcolor=CHART_THEME["paper_bgcolor"],
            plot_bgcolor=CHART_THEME["plot_bgcolor"],
            font=dict(color=CHART_THEME["font_color"], family="Inter, sans-serif"),
            margin=dict(l=20, r=20, t=40, b=20),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.2,
                xanchor="center",
                x=0.5,
            ),
        )
        fig.update_xaxes(gridcolor=CHART_THEME["gridcolor"], zeroline=False)
        fig.update_yaxes(gridcolor=CHART_THEME["gridcolor"], zeroline=False)
