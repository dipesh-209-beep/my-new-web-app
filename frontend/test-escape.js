const escapeHtml = (value) => {
  return value
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">")
    .replace(/"/g, "\"")
    .replace(/'/g, "'");
};

const test = 'New Road <script>alert(1)</script>';
console.log("Input:", test);
console.log("Output:", escapeHtml(test));
