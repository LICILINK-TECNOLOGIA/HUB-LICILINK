(function () {
    "use strict";
    // Issue #71: auto-submit do formulário de handoff. Localiza o
    // formulário só pelo identificador fixo do DOM e chama a submissão
    // nativa (`HTMLFormElement.submit()`) - nunca lê, copia, serializa,
    // registra ou altera nenhum campo (o código continua existindo
    // somente no `value` já renderizado pelo servidor). Sem `fetch`,
    // sem montar query string, sem nenhuma dependência externa. Uma
    // falha aqui (formulário ausente, `submit()` bloqueado) é sempre
    // silenciosa - o botão "Continuar" já renderizado no formulário
    // continua disponível como alternativa manual.
    var form = document.getElementById("handoff-form");
    if (form) {
        try {
            form.submit();
        } catch (error) {
            // Falha silenciosa - ver comentário acima.
        }
    }
})();
