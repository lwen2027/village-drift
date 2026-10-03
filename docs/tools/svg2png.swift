// SVG -> PNG, using AppKit's own SVG support.
//
// Needed because this box has no rsvg-convert, cairosvg, PIL or matplotlib,
// Homebrew refuses to install without a sudo chown across /opt/homebrew, and
// both headless Chrome and qlmanage are blocked ("bootstrap_check_in ...
// Permission denied", "sandbox initialization failed"). Swift ships with the
// OS, and NSImage has read SVG since Big Sur.
//
//   swift eval/svg2png.swift in.svg out.png 2872
//
// The width argument matters: NSImage rasterises an SVG at its natural size
// (940pt here), which is unreadable for a chart full of 11pt annotations.
// Drawing into an explicitly sized bitmap rep scales the VECTORS rather than
// upscaling a small raster, so the text stays sharp.

import AppKit
import Foundation

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: svg2png <in.svg> <out.png> [width]\n".data(using: .utf8)!)
    exit(2)
}
let inPath = args[1], outPath = args[2]
let targetW = args.count > 3 ? Int(args[3]) ?? 2872 : 2872

guard let img = NSImage(contentsOfFile: inPath) else {
    FileHandle.standardError.write("could not load \(inPath)\n".data(using: .utf8)!)
    exit(1)
}
let natural = img.size
guard natural.width > 0, natural.height > 0 else {
    FileHandle.standardError.write("loaded image has zero size\n".data(using: .utf8)!)
    exit(1)
}
let scale = CGFloat(targetW) / natural.width
let w = targetW, h = Int((natural.height * scale).rounded())

guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else {
    FileHandle.standardError.write("could not allocate bitmap\n".data(using: .utf8)!)
    exit(1)
}
rep.size = NSSize(width: w, height: h)

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
// The chart's own background rect covers the canvas, but fill white first so a
// transparent margin never renders black in viewers that ignore alpha.
NSColor.white.setFill()
NSRect(x: 0, y: 0, width: w, height: h).fill()
img.draw(in: NSRect(x: 0, y: 0, width: w, height: h),
         from: NSRect(origin: .zero, size: natural),
         operation: .sourceOver, fraction: 1.0)
NSGraphicsContext.restoreGraphicsState()

guard let data = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("PNG encode failed\n".data(using: .utf8)!)
    exit(1)
}
try data.write(to: URL(fileURLWithPath: outPath))
print("wrote \(outPath)  \(w)x\(h)  (natural \(Int(natural.width))x\(Int(natural.height)), \(String(format: "%.2f", scale))x)")
