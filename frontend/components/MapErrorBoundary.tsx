"use client";

import { Component, ErrorInfo, ReactNode } from "react";
import { ChevronIcon, LayersIcon } from "@/components/icons/TransitIcons";

interface Props {
  children: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class MapErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("MapErrorBoundary caught an error:", error, errorInfo);
    this.props.onError?.(error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          className="flex h-full w-full flex-col items-center justify-center gap-4 rounded-xl border border-route-line bg-surface-raised p-6 text-center"
        >
          <LayersIcon size={48} className="text-ink-tertiary" />
          <div>
            <h2 className="text-lg font-semibold text-ink">Map failed to load</h2>
            <p className="mt-2 text-sm text-ink-secondary">
              {this.state.error?.message ?? "An unexpected error occurred while rendering the map."}
            </p>
          </div>
          <button
            type="button"
            onClick={this.handleRetry}
            className="inline-flex items-center gap-2 rounded-md bg-brand px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-brand-dark"
          >
            <ChevronIcon direction="up" size={16} />
            Reload map
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}