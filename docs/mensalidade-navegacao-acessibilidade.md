# Mensalidade, navegação interna e acessibilidade

## Mensalidade do plano

**Área do Ponto → Configurações → Plano ME GUARDE Mais → Valor da mensalidade (R$)**. Somente administrador autorizado pode salvar, inclusive por POST direto. O valor inicial é R$ 33,90; valores positivos com até duas casas decimais são aceitos, dentro da capacidade de armazenamento monetário. A mensagem é Mensalidade atualizada com sucesso.

A configuração fica em `settings.monthly_price_cents`, persistida em centavos. Uma alteração não exige novo deploy: as páginas públicas, comparativos, telas do plano e próximos lembretes usam o valor vigente. Benefícios e regras de créditos/ciclos permanecem os mesmos. Cobranças novas guardam o valor e o texto comercial efetivamente apresentados; versões comerciais identificam a mensalidade, sem alterar o contrato institucional ou aceites anteriores. Checkout desatualizado pede nova conferência de preço.

Pedidos já emitidos, inclusive pendentes, mantêm seus valores. QR Code, validação da resposta inicial da API, confirmação por webhook/consulta, conferência manual, auditoria e documentos usam o valor do próprio pedido. Alterar a configuração não quita nada, não modifica ciclos ativos nem antecipa créditos da renovação. O histórico mostra os preços reais de cada pagamento.

## Navegação e perfis

O site público mantém identidade institucional, banners e acessos a Dúvidas, Blog, cadastro e às duas telas de login. Atalhos Como funciona, Preços e planos e Prazos e dúvidas ajudam a localizar informações. Dentro dos painéis, o cabeçalho mostra navegação interna e Sair. Gerenciar blog aparece somente para quem tem a permissão existente de edição. Recebimento e retirada também respeitam as permissões do funcionário na apresentação e nas rotas diretas. O cliente continua restrito aos próprios registros.

Pendências e autorizações usa uma largura comum, colunas equivalentes e caixas de seleção dimensionadas sem empurrar o texto para fora. Instruções, títulos, descrições, ícones e formulários se adaptam ao celular. Tipografia principal de referência 16 px, ícones consistentes por contexto, foco visível e controles de toque compartilham regras de CSS. Não foram reescritos textos contratuais, assinaturas ou evidências.

## Reordenar pagamentos

A interface mantém somente a alça de seis pontos e o número. Arraste com mouse ou dedo; com a alça em foco, use ↑/↓. A rolagem acompanha o gesto nas bordas da tela. Um simples toque não reordena. A numeração muda na hora, mas somente Salvar alterações grava. Os três contextos permanecem independentes e os campos seguem vinculados à modalidade. Sem JavaScript, permanece o seletor de prioridade.

## Validação

Suíte completa: **435 aprovados**, incluindo PostgreSQL isolado, migração SQLite → PostgreSQL, valores antigos/novos, rejeição de valor divergente, renovação antecipada, segurança e integrações simuladas. Rodada final de preço/perfis: 13 aprovados e 1 teste PostgreSQL não executado nessa rodada específica (já executado na suíte completa); inclui dois testes adicionais de permissões operacionais. As rodadas se sobrepõem.

Chromium em 320, 360, 390, 430, 768, 1024 e 1440 px: visitante, cliente, funcionário e administrador; login/saída, navegação, recebimento, assinatura preservada no redimensionamento, retirada, rascunho de pendência com texto longo e mensalidade. Outro cenário valida arraste real de mouse/toque, setas e salvar/recarregar nas três abas. Sem erros JavaScript ou rolagem horizontal. APIs simuladas; sem cobranças ou e-mails reais. A emulação não substitui aparelhos físicos/Safari.

## Oracle

Sem novas credenciais, dependências ou agendamentos. Faça o backup habitual do banco e uploads, revise/mescle a branch, atualize o checkout e reinicie o serviço existente:

```bash
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip check
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

A inicialização acrescenta apenas a coluna de mensalidade com default R$ 33,90, sem regravar pedidos/ciclos. Inicialize o schema sequencialmente antes de iniciar múltiplos workers, conforme o procedimento existente. O migrador preserva a mensalidade configurada ao copiar sobre um destino ainda padrão; divergências de preço com destino já personalizado interrompem a migração para conferência.

Não altere `/etc/ponto-coleta/site.env` nem credenciais Google/Gmail/FastFlow. A Oracle não foi acessada ou reiniciada nesta implementação.
