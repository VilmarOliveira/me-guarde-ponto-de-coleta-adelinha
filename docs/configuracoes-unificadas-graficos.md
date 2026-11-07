# Configurações unificadas e gráficos

A grade de Configurações usa cartões compactos com ícones, foco de teclado, largura igual e altura uniforme em cada linha. Quatro colunas acima de 1100 px, duas de 601 a 1100 px e uma até 600 px. O cartão inteiro é um link, sem sublinhado ou corte de palavras.

**Identidade visual** reúne, nesta ordem, Imagem do cabeçalho, Banners, Fotos do local, Aparência e Cartões explicativos. As abas reutilizam as rotas, formulários, validações e redirecionamentos existentes; portanto salvamento, substituição e restauração continuam isolados por seção. Os atalhos individuais foram removidos apenas da grade. URLs antigas continuam válidas.

**Pagamentos** abre em Ordem e limites e mantém as três configurações independentes por contexto. Chave Pix reutiliza a consulta/edição existente. As permissões continuam no servidor: pagamentos/chave Pix, banners e cartões explicativos permanecem exclusivos do administrador conforme suas regras anteriores. Abas sem permissão ficam ocultas. Ambas as áreas têm Voltar para Configurações e identificam a aba atual, inclusive após salvar.

O botão Ver histórico completo usa laranja e branco; destino, posição, duas últimas retiradas e abertura em Hoje foram preservados.

## Métricas

Barras é o tipo inicial. Barras mostra visitas por dia; em períodos superiores a 62 dias, por mês. Pizza mostra participação das visitas por dispositivo, com nome, quantidade e porcentagem na legenda. Ambos usam o mesmo universo de pageviews e o período aplicado no painel. A troca preserva os parâmetros do filtro e exibe somente um gráfico. Sem visitas, mostra Sem dados para o período selecionado. Quantidades também aparecem em texto e não dependem apenas das cores. Não há biblioteca de gráficos externa nem novas ferramentas de rastreamento.

## Validação e Oracle

68 testes aprovados: navegação, permissões, aba após salvar, isolamento das três configurações de pagamentos, cálculos/gráficos, período preservado, ausência de dados e regressão dos formulários de imagens e Pix. Chromium validado em 320, 360, 390, 430, 768, 1024 e 1440 px: grade 4/2/1, alturas/larguras, foco visível, abas, salvamento, gráficos e filtros; sem erros JavaScript ou rolagem horizontal. Pagamentos simulados, sem cobranças reais. Emulação não substitui aparelhos físicos/Safari.

Não há novas variáveis, dependências, tabelas ou agendamentos. Após revisão e merge, atualize o checkout na Oracle e reinicie o serviço pelo procedimento habitual, preservando banco/uploads e `/etc/ponto-coleta/site.env`:

```bash
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip check
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

A Oracle não foi acessada. Não houve alteração nas validações ou no processamento dos pagamentos.
