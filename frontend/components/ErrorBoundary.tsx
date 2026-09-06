"use client";

import { Component, ErrorInfo, ReactNode } from "react";
import { ChevronIcon, TransferIcon } from "@/components/icons/TransitIcons";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div
          role="alert"
          className="flex flex-col gap-4 rounded-xl border border-red-200 bg-red-50 p-6 text-center"
        >
          <TransferIcon size={48} className="mx-auto text-red-500" />
          <div>
            <h2 className="text-lg font-semibold text-red-800">Something went wrong</h2>
            <p className="mt-2 text-sm text-red-700">
              {this.state.error?.message ?? "An unexpected error occurred"}
            </p>
            <details className="mt-4 text-left text-xs text-red-600">
              <summary className="cursor-pointer">Error details</summary>
              <pre className="mt-2 p-3 bg-red-100 rounded overflow-auto max-h-40">
                {this.state.error?.stack}
              </pre>
            </details>
          </div>
          <button
            type="button"
            onClick={this.handleRetry}
            className="mx-auto inline-flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
          >
            <ChevronIcon direction="up" size={16} />
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}