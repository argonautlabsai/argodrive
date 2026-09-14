# Connection map

Open **Topology** in the native app in either Reports Only or Live Hardware mode.
Reports Only shows a **Snapshot · sampling off** map without starting a sampler.
Enable **Monitor → Live Hardware** only when live read metrics are wanted.
The map shows the Mac, its reported ports, attached Thunderbolt/USB4 hubs and
peripherals, enclosures and built-in storage. Select any node or connection row
to inspect its storage, path, read speed and timing. Empty ports can be hidden;
Zoom and Export map produce a scalable SVG diagram.

## Where measurements come from

- System Information supplies bus hierarchy, connection points, device names
  and reported link speeds. A disconnected port's “Up to” rating is not shown
  as an active link. Port numbers are macOS identifiers, not left/right labels.
- IORegistry supplies each whole physical disk's nearest PCI tunnel identity.
  It is matched to the Thunderbolt switch identity. This distinguishes identical
  enclosures without guessing from volume names or enumeration order. Disk
  images and partition slices are excluded. Tunnel identities are discarded
  after joining. When IORegistry exposes an NVMe controller serial, storage
  records retain its SHA-256 identity so a replug or rename can be matched; raw
  serials are not included in the map/API/export. The hash is a stable device
  identifier, not an anonymisation guarantee. Missing serials remain unknown.
- Unmatched storage uses a dashed, explicitly unresolved association. It does
  not establish a cable or a hub connection. Historical layouts are not silently
  substituted for the currently connected hardware.
- Read throughput uses existing aligned physical-device counters. Parent nodes
  sum only aligned, present, measured descendants. **Gb/s** is the reported link
  rate; **GB/s** is measured storage throughput. They are not equivalent limits.
- Average read time is the last five seconds' change in cumulative read time
  divided by completed read operations. The driver's time counter is in
  nanoseconds, as documented by [Apple's IOBlockStorageDriver header](https://github.com/apple-oss-distributions/IOStorageFamily/blob/main/IOBlockStorageDriver.h).
  This metric includes storage-driver processing; it does not measure cable RTT,
  isolated hub delay, GPU wait or a percentile. A downstream drive's read time
  may appear on multiple path rows; it must not be added across those rows.
- Read IOPS and mean request size use the same recent operation window. No
  completed reads, missing counters, counter resets and stale measurements stay
  unavailable where a mean cannot be established.

## Collection cost and scope

Topology discovery runs on demand when the map is opened. In Reports Only mode,
the snapshot is retained until **Rescan connections** is pressed or the backend
restarts; live mode keeps the existing 60-second cache. **Rescan connections**
refreshes wiring and volume identities after moving a cable. It runs outside the server's report lock, so
discovery does not block live data requests. Normal live updates reuse the
existing sampler and do not run System Information or IORegistry each second.
Reports-only startup runs no hardware discovery or sampler. Opening Topology
reads device metadata only; it does not read model weights, start performance
collection, or launch an inference/drive benchmark. Live metrics remain unavailable.

## Shared uplinks (12 September 2026)

Detected hub and port nodes with at least two mapped physical SSDs show
**Shared uplink**. Select either SSD or the hub to see the member drives,
reported link rate and whether a shared read limit has been calibrated.
Membership comes from discovered ancestry, not identical SSD models, drive
colours or an older cable layout. Unknown enclosure associations stay unresolved.

Blue is supported as a named drive role and keeps that role across BSD-number
changes. Role names are presentation labels; the serial-derived identity is the
physical identifier when available. The user-confirmed Blue/Yellow hub layout
must still be discovered on the machine before it appears as live evidence.

The calibration planner in `monitor/optimizer_storage.py` now schedules single
drive tests, distinct shared-uplink member tests, and an all-selected test.
A nested hub and port with identical members use one simultaneous test. A
five-drive layout with Blue and Yellow sharing an uplink therefore has five
individual tests, one Blue+Yellow test, and one all-five test. This is planner
support; the full optimizer runner and its interface are still being integrated.
No new measured ceiling or five-drive token-speed result is asserted.

Live also no longer labels the sum of separately configured drive ceilings as
a calibrated multi-drive ceiling. The aggregate stays uncalibrated pending a
simultaneous measurement; measured aggregate reads and individual-drive ceilings
remain available.

This is a local hardware map. Cluster membership, RDMA, peer RAM caches and remote
Mac discovery remain future functionality. The hub and peripheral representations
can accommodate those future connections without asserting that they exist now.

## Verified on 10 September 2026

- Apple M5 Max MacBook Pro, 128 GB, with internal storage.
- Green: OWC Express 1M2 80G on macOS Port 1, reported 80 Gb/s.
- White: another OWC Express 1M2 80G on macOS Port 3, reported 80 Gb/s.
- Port 2 empty; no hub present in the current inventory.
- Real eight-second read-only UI check: throughput, operation rate and read time
  populated for all three SSDs. Source file size, mtime, inode and device identity
  remained unchanged. Evidence: `.build/topology-test/`. No inference or ceiling
  benchmark is claimed by this check.
- Automated fixtures cover a hub with two identical downstream enclosures,
  missing identities, disk-image exclusion, empty ports, aligned branch totals,
  reset/stale/idle timing and reports-only isolation. Physical hub hot-plug testing
  remains to be performed with a hub attached.

## Centred layout and scaling (12 September 2026)

The host sits at the centre of the map, with its ports, storage and hubs arranged
around it. Small hubs fan their terminal drives to either side to keep the map
compact. Branch spans reserve space for nested hubs and additional drives;
placement is logical and does not imply a physical left/right Mac socket.

**Fit to window** uses both the canvas width and height. The other zoom options
are percentages of the diagram's native size: 100% keeps card sizes consistent
between windows. Changing zoom recentres the Mac; normal counter updates preserve
horizontal/vertical scroll and keyboard focus. Scroll inside a zoomed map to pan.
The inspector moves below the map in narrower windows. Link ratings and measured
read throughput use separate rows, and SVG exports retain the complete diagram.

Validation used the explicitly labelled five-drive fixture in
`tests/fixtures/topology-five-drive.json`, including Blue and Yellow on one hub.
This is layout evidence, not a new discovery or performance measurement. Browser
checks at 760, 1280 and 1760 px cover Fit with no canvas overflow, 150% zoom,
selection, shared-uplink details, live-refresh scroll retention and light/dark
appearance. Geometry tests also cover nested hubs, additional SSDs, empty-port
filtering, unknown associations, finite connection paths and stable ordering.
