# Conta, plano e carregamento — 8/11/2026

Base: main após PR #62, preservando a extração de QR/validade da resposta de criação e a confirmação do encerramento pela API.

- Primeiro cartão: título **Cadastre-se em nosso site**, sem o parágrafo anterior. O novo WebP de 800 × 1200 com carimbo não veio anexado; a imagem existente foi preservada. Substituição continua em Configurações → Identidade visual → Cartões explicativos, com limite/miniaturas/ampliação existentes. Não foi inventada uma arte substituta.
- Nome exibido: **ME GUARDE+**, sem alterar nomes técnicos nem evidências comerciais armazenadas. Botões Pix Fast: **ASSINAR PLANO AGORA** ou **RENOVAR PLANO AGORA**, conforme histórico de ciclos.
- Cabeçalho público: LOGIN utiliza o acesso já existente da Área do Cliente, evitando atalhos duplicados. Visitante vai ao login; cliente autenticado vai à sua área. Área do Ponto permanece separada. No celular, LOGIN está no menu responsivo.
- Meus dados → Desvincular conta Google: confirmação explícita e verificação da senha atual. Sem senha, é necessário criar/verificar senha pelo fluxo de recuperação por e-mail antes de desvincular. Invalida sessões anteriores e vínculos pendentes, mantendo a sessão atual e todos os registros. Voltar pelo Google exige novamente o vínculo explícito com senha.
- QR vencido desaparece pelo horário local da validade normalizada e pela verificação no servidor. A tela continua consultando, sem parar definitivamente após dez minutos. Pendência/indisponibilidade não autoriza uma nova cobrança.
- **Gerar novo Pix** aparece apenas após encerramento sem pagamento confirmado. O servidor verifica novamente ou reaproveita uma confirmação recente dentro da janela de proteção da API. Cada tentativa tem identificador financeiro/callback próprios e aponta ao mesmo pedido comercial (`root_order_id` e `retry_of`); valor e regras originais ficam preservados. A cobrança anterior não é sobrescrita. Bloqueio por cliente e reaproveitamento da tentativa existente impedem duplicação. Pagamento/ativação continuam validados na API e limitados a um ciclo por finalidade.

## Imagens e medição

Primeiro banner usa prioridade alta e srcset de 640/1280/2048 px. Somente após carregar ele, o próximo é preparado; os demais aguardam. Mantidos proporção, toque, indicadores, intervalo de três segundos, pausas e preferência de movimento reduzido. Originais ficam intactos; versões WebP são geradas em cache limitado de memória. Quando uma conversão sem redução de dimensões ficaria maior, conserva-se o original. URLs mantêm a revisão de upload, ETag/304 e cache público; páginas privadas continuam `private, no-store`.

Medição Chromium local, cache frio, três rodadas por tamanho, mesmos três banners JPEG sintéticos de 2048 × 684; conexão de 1,6 Mbps e latência de 150 ms; fontes/serviços externos bloqueados. Medianas: resposta completa da primeira imagem; volume de recursos concluídos até um segundo depois dela. Não é medição de produção nem de toda a navegação; downloads em andamento não entram no volume concluído.

| Tela | Primeiro banner antes | Depois | Dados concluídos antes | Depois |
| --- | ---: | ---: | ---: | ---: |
| Celular 390 px | 13,72 s | 1,36 s | 2,66 MB | 0,26 MB |
| Computador 1440 px | 16,30 s | 9,05 s | 2,66 MB | 1,12 MB |

Reproduzir: `PYTHONPATH=/caminho/playwright python tests/browser/loading_measure.py /tmp/resultado.json`. Comparar com a revisão anterior usando a mesma fixture e configuração.

## Validação e Oracle

Testes usam dados e API simulados, sem enviar e-mails ou gerar cobranças reais. `tests/test_eight_updates.py` cobre Pix pago/expirado/cancelado/pendente/indisponível em primeira contratação e renovação, repetição, preço preservado, desvinculação e cache. O teste Google adicional confirma que a conta desvinculada não volta a ser ligada automaticamente. `tests/browser/eight_updates.py` confere 320, 360, 390, 430, 768, 1024 e 1440 px, navegação, carrossel, conta, renovação e vencimento do QR em tela. A emulação não substitui testes em aparelhos físicos.

Após merge, na pasta da aplicação e com checkout limpo: `git switch main`, `git pull --ff-only origin main` e reinício do serviço existente (`sudo systemctl restart ponto-coleta.service`, ou o nome efetivo da instalação). Faça o backup habitual e confira o status do serviço. A inicialização adiciona somente duas colunas de referência de tentativas em `plan_orders`; execute sequencialmente antes de múltiplos workers, conforme procedimento existente. Sem novas variáveis, credenciais, dependências ou mudanças nos agendamentos. Oracle não foi acessada nesta implementação.
