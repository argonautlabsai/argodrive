import AppKit
import Foundation
let out = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for size in [16,32,128,256,512] {
    for scale in [1,2] {
        let pixels = size * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        let transform = AffineTransform(scale: CGFloat(pixels)/1024)
        (transform as NSAffineTransform).concat()
        NSColor(calibratedRed: 0.37, green: 0.31, blue: 0.86, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 72,y: 72,width: 880,height: 880),xRadius: 192,yRadius: 192).fill()
        let a = NSBezierPath()
        a.move(to: NSPoint(x: 260,y: 285));a.line(to: NSPoint(x: 512,y: 778));a.line(to: NSPoint(x: 764,y: 285));a.line(to: NSPoint(x: 605,y: 285));a.line(to: NSPoint(x: 512,y: 474));a.line(to: NSPoint(x: 419,y: 285));a.close()
        NSColor.white.setFill();a.fill()
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        try rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
