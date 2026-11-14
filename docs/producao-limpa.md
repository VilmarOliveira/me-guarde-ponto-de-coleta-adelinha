# Preparação de produção vazia

O comando é manual e separado da inicialização e do serviço web. Requer outro
banco PostgreSQL já inicializado pelo mesmo código. Por padrão apenas confere;
`--apply --backup /caminho/arquivo.dump` prepara exclusivamente o destino.

```bash
sudo .venv/bin/python scripts/prepare_clean_production.py
sudo .venv/bin/python scripts/prepare_clean_production.py --apply --backup /var/backups/ponto-coleta/teste-20261114-154946.dump
```

As conexões são lidas dos arquivos protegidos `/etc/ponto-coleta/site.env`
(origem) e `/etc/ponto-coleta/site-producao.env` (destino). Não são passadas na
linha de comando ou exibidas. O backup é conferido pelo pg_restore.

A origem fica em transação somente leitura. O destino é bloqueado durante a
preparação e a cópia é transacional. O comando recusa bancos iguais, destino com
dados operacionais, ausência de administrador principal único ou repetição de
uma preparação já concluída. Nenhuma tabela da origem é limpa.

Preserva configurações, preços, temas, imagens, políticas de pagamento, catálogo
editorial e o documento contratual vigente. O documento é republicado somente
como definição inicial, com audiência explícita vazia: campanhas, destinatários,
aceites e assinaturas antigos não são transferidos. O contrato padrão do código
é usado quando a origem não possui um documento publicado vigente.

A única conta copiada é o administrador principal: ID interno 1 e ADMPCA-1,
mesmo e-mail, senha e permissões. A sessão anterior é invalidada. Não copia contas
de clientes, outros membros da equipe, códigos de recuperação, pagamentos,
créditos, pacotes, retiradas, comprovantes, auditorias nem estatísticas. Desativa
o acesso administrativo legado no banco novo. As sequências internas são
recalculadas sobre as linhas do destino; clientes, pacotes e equipe nova começam
na primeira numeração do padrão atual (CLIPCA, PCTPCA, ATEPCA, GERPCA). O próximo
administrador é ADMPCA-2.

Uma SECRET_KEY nova é gravada atomicamente somente no arquivo preparado, com
permissão 600, antes do commit. Outras credenciais são preservadas. Isso impede
que cookies do ambiente antigo autentiquem IDs reutilizados na produção. Se a
transação falhar depois dessa gravação, apenas a chave do arquivo preparado
poderá ter mudado; o serviço em operação continua usando seu arquivo anterior.
Sequências PostgreSQL são não transacionais; uma tentativa interrompida pode
alterar suas posições no destino, recalculadas ao repetir a preparação.

O comando não ativa o banco nem reinicia serviços. A troca final exige validação
do destino, cópia protegida da configuração ativa, atualização do EnvironmentFile
usado por todas as rotinas e reinício coordenado dos serviços. O banco antigo
permanece separado e nunca deve ser usado por um ambiente de testes com envios
de e-mail ou cobranças reais habilitados.
