
const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".site-nav");
if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
}
document.querySelectorAll(".thumbs button").forEach((button) => {
  button.addEventListener("click", () => {
    const main = document.getElementById("product-main");
    if (main) main.src = button.dataset.src;
  });
});
function messageFrom(form) {
  const data = new FormData(form);
  return [
    "Olá Apinil, vim pelo site.",
    `Nome: ${data.get("nome") || ""}`,
    `E-mail: ${data.get("email") || ""}`,
    `Telefone: ${data.get("telefone") || ""}`,
    "",
    String(data.get("mensagem") || ""),
  ].join("\n");
}
const form = document.getElementById("contact-form");
if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = encodeURIComponent(messageFrom(form));
    window.open(`https://api.whatsapp.com/send?phone=556235845787&text=${text}`, "_blank", "noopener");
  });
  const mail = document.getElementById("mailto-btn");
  mail?.addEventListener("click", () => {
    if (!form.reportValidity()) return;
    const text = encodeURIComponent(messageFrom(form));
    window.location.href = `mailto:apinilindustria@hotmail.com?subject=${encodeURIComponent("Contato pelo site Apinil")}&body=${text}`;
  });
}
