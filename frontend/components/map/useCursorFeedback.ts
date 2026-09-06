"use client";

import { useEffect } from "react";
import { StopPickTarget } from "@/types/route";

interface UseCursorFeedbackProps {
  mapContainerRef: React.RefObject<HTMLDivElement | null>;
  pickTarget: StopPickTarget;
}

export function useCursorFeedback({
  mapContainerRef,
  pickTarget,
}: UseCursorFeedbackProps) {
  useEffect(() => {
    const container = mapContainerRef.current;
    if (!container) return;
    container.style.cursor = pickTarget ? "crosshair" : "";
  }, [mapContainerRef, pickTarget]);
}