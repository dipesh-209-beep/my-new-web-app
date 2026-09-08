import { vi } from "vitest";

export interface MockMarker {
  kind: "marker" | "circleMarker";
  latlng: unknown;
  options: Record<string, unknown>;
  tooltip: { content: string; options?: unknown } | null;
  popup: { content: string; options?: unknown } | null;
  handlers: Record<string, (() => void)[]>;
  removed: boolean;
}

export interface MockPolyline {
  points: unknown;
  options: Record<string, unknown>;
  popup: { content: string; options?: unknown } | null;
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
  };
  leafletState.markers.push(marker);

  const api = {
    ...marker,
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
    setStyle: (opts: Record<string, unknown>) => {
      Object.assign(marker.options, opts);
      return api;
    },
    setIcon: (icon: unknown) => {
      marker.options.icon = icon;
      return api;
    },
    addTo: () => api,
    remove: () => {
      marker.removed = true;
    },
  };
  return api;
}

export function createLeafletMock() {
  function makeMapApi(): MockMapInstance & Record<string, unknown> {
    const instance: MockMapInstance = {
      fitBoundsCalls: [],
      zoom: 12,
      handlers: {},
      fireZoomEnd(newZoom: number) {
        this.zoom = newZoom;
        (this.handlers["zoomend"] ?? []).forEach((h) => h());
      },
    };
    const api = {
      ...instance,
      setView: vi.fn().mockReturnThis(),
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
          return api;
        },
        addLayer: (layer: unknown) => {
          group.layers.push(layer);
          return api;
        },
        remove: () => {
          group.removed = true;
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
      const poly: MockPolyline = { points, options, popup: null };
      leafletState.polylines.push(poly);
      const api = {
        ...poly,
        bindPopup: (content: string, popupOptions?: unknown) => {
          poly.popup = { content, options: popupOptions };
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
