import { vi } from "vitest";

export interface MockMarker {
  kind: "marker" | "circleMarker";
  latlng: unknown;
  options: Record<string, unknown>;
  tooltip: { content: string; options?: unknown } | null;
  popup: { content: string; options?: unknown } | null;
  handlers: Record<string, (() => void)[]>;
  removed: boolean;
  /** The marker's icon element. `null` until the marker is added to a live
   * layer, matching Leaflet, where `Marker._initIcon` runs in `onAdd`. It is
   * replaced with a new element when a removed marker is re-added. */
  element: HTMLElement | null;
  /** Set by `openPopup()`; assertions read this instead of a real popup. */
  popupOpened: boolean;
  /** Incremented by `openPopup()`; lets tests assert activation fires once. */
  popupOpenCount: number;
}

export interface MockPolyline {
  points: unknown;
  options: Record<string, unknown>;
  popup: { content: string; options?: unknown } | null;
  handlers: Record<string, (() => void)[]>;
}

export interface MockLayerGroup {
  layers: unknown[];
  addedToMap: boolean;
  removed: boolean;
}

export interface MockControl {
  options: Record<string, unknown>;
  div: HTMLElement | null;
  addedToMap: boolean;
  removed: boolean;
}

export interface MockMapInstance {
  fitBoundsCalls: unknown[][];
  zoom: number;
  handlers: Record<string, (() => void)[]>;
  /** Spy, called once on init and again whenever the app recentres/zooms. */
  setView: ReturnType<typeof vi.fn>;
  /** Spy for `invalidateSize()`, which the page calls on sheet changes. */
  invalidateSize: ReturnType<typeof vi.fn>;
  /** Test helper: simulate the map firing "zoomend" after a zoom change. */
  fireZoomEnd(newZoom: number): void;
}

/** Reset before each test via `leafletState.reset()`. */
export const leafletState = {
  markers: [] as MockMarker[],
  polylines: [] as MockPolyline[],
  layerGroups: [] as MockLayerGroup[],
  controls: [] as MockControl[],
  mapInstances: [] as MockMapInstance[],
  reset() {
    this.markers = [];
    this.polylines = [];
    this.layerGroups = [];
    this.controls = [];
    this.mapInstances = [];
  },
};

function makeMarker(kind: MockMarker["kind"], latlng: unknown, options: Record<string, unknown> = {}): MockMarker & Record<string, unknown> {
  const marker: MockMarker = {
    kind,
    latlng,
    options,
    tooltip: null,
    popup: null,
    handlers: {},
    removed: false,
    element: null,
    popupOpened: false,
    popupOpenCount: 0,
  };
  leafletState.markers.push(marker);

  const fire = (event: string) => {
    marker.handlers[event]?.forEach((handler) => handler());
  };

  // `addToMap`/`removeFromMap` back the mock's layer-group lifecycle. Leaflet
  // creates the icon element in `onAdd` (firing "add") and destroys it in
  // `onRemove` (firing "remove"); a re-add builds a fresh element.
  const addToMap = () => {
    marker.element = document.createElement("div");
    marker.removed = false;
    fire("add");
  };
  const removeFromMap = () => {
    marker.element = null;
    marker.removed = true;
    fire("remove");
  };

  const api = {
    ...marker,
    getElement: () => marker.element ?? undefined,
    addToMap,
    removeFromMap,
    openPopup: () => {
      marker.popupOpened = true;
      marker.popupOpenCount += 1;
      return api;
    },
    closePopup: () => {
      marker.popupOpened = false;
      return api;
    },
    bindTooltip: (content: string, tooltipOptions?: unknown) => {
      marker.tooltip = { content, options: tooltipOptions };
      return api;
    },
    bindPopup: (content: string, popupOptions?: unknown) => {
      marker.popup = { content, options: popupOptions };
      return api;
    },
    on: (event: string, handler: () => void) => {
      (marker.handlers[event] ??= []).push(handler);
      return api;
    },
    off: (event: string, handler: () => void) => {
      const list = marker.handlers[event];
      if (list) marker.handlers[event] = list.filter((h) => h !== handler);
      return api;
    },
    setStyle: (opts: Record<string, unknown>) => {
      Object.assign(marker.options, opts);
      return api;
    },
    setIcon: (icon: unknown) => {
      marker.options.icon = icon;
      // Leaflet rebuilds the icon element on setIcon while the marker is on a
      // map; model the replacement so handlers on the old element are lost.
      if (marker.element) {
        marker.element = document.createElement("div");
        fire("add");
      }
      return api;
    },
    addTo: () => {
      addToMap();
      return api;
    },
    remove: () => removeFromMap(),
  };
  return api;
}

/** Attach/detach a child layer to the map, if it knows how (markers do). */
function attachLayer(layer: unknown): void {
  (layer as { addToMap?: () => void }).addToMap?.();
}
function detachLayer(layer: unknown): void {
  (layer as { removeFromMap?: () => void }).removeFromMap?.();
}

export function createLeafletMock() {
  function makeMapApi(): MockMapInstance & Record<string, unknown> {
    const setView = vi.fn().mockReturnThis();
    const instance: MockMapInstance = {
      fitBoundsCalls: [],
      zoom: 12,
      handlers: {},
      setView,
      invalidateSize: vi.fn(),
      fireZoomEnd(newZoom) {
        this.zoom = newZoom;
        this.handlers["zoomend"]?.forEach((h) => h());
      },
    };
    const api = {
      ...instance,
      setView,
      on: vi.fn((event: string, handler: () => void) => {
        (instance.handlers[event] ??= []).push(handler);
        return api;
      }),
      remove: vi.fn(),
      fitBounds: vi.fn((bounds: unknown) => {
        instance.fitBoundsCalls.push([bounds]);
      }),
      addLayer: vi.fn(),
      getZoom: vi.fn(() => instance.zoom),
      fireZoomEnd: (newZoom: number) => instance.fireZoomEnd(newZoom),
    };
    return api as unknown as MockMapInstance & Record<string, unknown>;
  }

  const L = {
    map: vi.fn(() => {
      const mapApi = makeMapApi();
      leafletState.mapInstances.push(mapApi);
      return mapApi;
    }),
    tileLayer: vi.fn(() => ({ addTo: vi.fn().mockReturnThis() })),
    layerGroup: vi.fn(() => {
      const group: MockLayerGroup = { layers: [], addedToMap: false, removed: false };
      leafletState.layerGroups.push(group);
      const api = {
        addTo: () => {
          group.addedToMap = true;
          group.removed = false;
          group.layers.forEach((layer) => attachLayer(layer));
          return api;
        },
        addLayer: (layer: unknown) => {
          group.layers.push(layer);
          if (group.addedToMap) attachLayer(layer);
          return api;
        },
        removeLayer: (layer: unknown) => {
          const index = group.layers.indexOf(layer);
          if (index >= 0) group.layers.splice(index, 1);
          detachLayer(layer);
          return api;
        },
        remove: () => {
          group.removed = true;
          group.layers.forEach((layer) => detachLayer(layer));
          return api;
        },
      };
      return api;
    }),
    circleMarker: vi.fn((latlng: unknown, options: Record<string, unknown> = {}) =>
      makeMarker("circleMarker", latlng, options)
    ),
    marker: vi.fn((latlng: unknown, options: Record<string, unknown> = {}) =>
      makeMarker("marker", latlng, options)
    ),
    polyline: vi.fn((points: unknown, options: Record<string, unknown> = {}) => {
      const poly: MockPolyline = { points, options, popup: null, handlers: {} };
      leafletState.polylines.push(poly);
      const api = {
        ...poly,
        bindPopup: (content: string, popupOptions?: unknown) => {
          poly.popup = { content, options: popupOptions };
          return api;
        },
        setStyle: (opts: Record<string, unknown>) => {
          Object.assign(poly.options, opts);
          return api;
        },
        on: (event: string, handler: () => void) => {
          (poly.handlers[event] ??= []).push(handler);
          return api;
        },
        off: (event: string, handler: () => void) => {
          const list = poly.handlers[event];
          if (list) poly.handlers[event] = list.filter((h) => h !== handler);
          return api;
        },
        addTo: () => api,
      };
      return api;
    }),
    latLngBounds: vi.fn((points: unknown) => ({ __points: points })),
    divIcon: vi.fn((opts: Record<string, unknown>) => ({ __divIcon: true, ...opts })),
    DomUtil: {
      create: vi.fn((tag: string) => document.createElement(tag)),
    },
    DomEvent: {
      disableClickPropagation: vi.fn(),
      disableScrollPropagation: vi.fn(),
    },
    control: {
      scale: vi.fn((options: Record<string, unknown> = {}) => {
        const control: MockControl = { options, div: null, addedToMap: false, removed: false };
        leafletState.controls.push(control);
        const api = {
          addTo: () => {
            control.addedToMap = true;
            return api;
          },
          remove: () => {
            control.removed = true;
          },
        };
        return api;
      }),
    },
    Control: Object.assign(
      class {
        options: Record<string, unknown>;
        constructor(options: Record<string, unknown> = {}) {
          this.options = options;
        }
      },
      {
        extend: (proto: { onAdd: () => HTMLElement }) => {
          return class {
            options: Record<string, unknown>;
            control: MockControl;
            constructor(options: Record<string, unknown> = {}) {
              this.options = options;
              this.control = { options, div: null, addedToMap: false, removed: false };
              leafletState.controls.push(this.control);
            }
            addTo() {
              this.control.div = proto.onAdd();
              this.control.addedToMap = true;
              return this;
            }
            remove() {
              this.control.removed = true;
            }
          };
        },
      }
    ),
  };

  return { default: L, ...L };
}
