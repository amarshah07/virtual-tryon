const express = require("express");
const mysql = require("mysql2");
const cors = require("cors");

const app = express();
app.use(cors());
app.use(express.json());

// 🔹 FreeSQLDatabase credentials
const db = mysql.createConnection({
  host: "sql12.freesqldatabase.com",
  user: "sql12797547",
  password: "kgzZjtlxva",
  database: "sql12797547",
  port: 3306
});

db.connect(err => {
  if (err) {
    console.error("❌ DB Connection Failed:", err);
    return;
  }
  console.log("✅ Connected to MySQL Database!");
});

// Test API
app.get("/", (req, res) => {
  res.send("Backend is working!");
});

// Example: Fetch products
app.get("/products", (req, res) => {
  db.query("SELECT * FROM products", (err, results) => {
    if (err) return res.status(500).json(err);
    res.json(results);
  });
});

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => console.log(`🚀 Server running on port ${PORT}`));
