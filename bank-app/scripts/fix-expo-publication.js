const fs = require("fs")
const path = require("path")

const file = path.join(__dirname, "../node_modules/expo-modules-autolinking/build/platforms/android/android.js")
if (!fs.existsSync(file)) process.exit(0)
const before = "...(publication ? { publication } : {}),"
const after = "...(publication ? { publication: { ...publication, version: publication.version ?? revision.version } } : {}),"
const text = fs.readFileSync(file, "utf8")
if (text.includes(before)) fs.writeFileSync(file, text.replace(before, after))
