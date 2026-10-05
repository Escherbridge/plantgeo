"use client";

import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ReportErrorBoundaryProps {
  children: ReactNode;
  /** What stands in for the failed subtree; receives a reset that re-mounts the children. */
  fallback: (reset: () => void) => ReactNode;
  /** Changing this clears a caught error (e.g. a new message id or a reopened panel). */
  resetKey?: unknown;
}

interface ReportErrorBoundaryState {
  hasError: boolean;
}

/** Error boundary pattern: contains a render failure to one report or panel instead of blanking the map. */
export class ReportErrorBoundary extends Component<ReportErrorBoundaryProps, ReportErrorBoundaryState> {
  state: ReportErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ReportErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Regional analysis render failed', error, info.componentStack);
  }

  componentDidUpdate(previous: ReportErrorBoundaryProps) {
    if (this.state.hasError && previous.resetKey !== this.props.resetKey) this.setState({ hasError: false });
  }

  private reset = () => this.setState({ hasError: false });

  render() {
    return this.state.hasError ? this.props.fallback(this.reset) : this.props.children;
  }
}
