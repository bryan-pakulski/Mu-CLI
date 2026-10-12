import React from 'react';
import { StyleSheet, TextInput, TouchableOpacity, View } from 'react-native';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';

type Row = { key:string; text:string };
type Section = 'Acceptance criteria' | 'Checks';

function sectionPattern(title: Section) {
  return new RegExp(`(^## ${title}[^\\S\\n]*\\n)([\\s\\S]*?)(?=^## |$(?![\\s\\S]))`, 'im');
}

function rowsFrom(markdown: string, title: Section): Row[] {
  const body = sectionPattern(title).exec(markdown)?.[2] || '';
  return body.split('\n').filter(line => line.trim()).map((line, index) => {
    const match = title === 'Checks'
      ? /^\s*[-*]\s*suite:\s*(\S*)\s+cmd: ?(.*)$/.exec(line)
      : /^\s*[-*]\s*\[([^\]]*)\] ?(.*)$/.exec(line);
    return match ? {key:match[1], text:match[2]} : {
      key:title === 'Checks' ? `check${index+1}` : `AC-${index+1}`,
      text:line.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ''),
    };
  });
}

function replaceRows(markdown: string, title: Section, rows: Row[]) {
  const body = rows.map(row => title === 'Checks'
    ? `- suite: ${row.key}   cmd: ${row.text}` : `- [${row.key}] ${row.text}`).join('\n');
  const section = `## ${title}\n${body}\n\n`;
  return sectionPattern(title).test(markdown)
    ? markdown.replace(sectionPattern(title), () => section)
    : `${markdown.trimEnd()}\n\n${section}`;
}

/** Both editors update the same versioned Markdown draft; the server validates it. */
export function JobPlanEditor({ value, onChange }: {value:string; onChange:(value:string)=>void}) {
  const { colors } = useTheme();
  const input = {color:colors.text, borderColor:colors.hairline};
  const button = (label:string, fn:()=>void, disabled=false) => <TouchableOpacity key={label} accessibilityRole="button" accessibilityState={{disabled}} disabled={disabled} onPress={fn} style={[styles.button,{borderColor:colors.hairline,opacity:disabled?.4:1}]}><Text variant="xs">{label}</Text></TouchableOpacity>;
  return <View style={styles.root}>
    {(['Acceptance criteria','Checks'] as Section[]).map(title => {
      const rows = rowsFrom(value, title);
      const update = (next:Row[]) => onChange(replaceRows(value,title,next));
      const change = (index:number, patch:Partial<Row>) => update(rows.map((row,i)=>i===index?{...row,...patch}:row));
      const move = (index:number, offset:number) => { const next=[...rows]; [next[index],next[index+offset]]=[next[index+offset],next[index]]; update(next); };
      return <View key={title} style={styles.root}><Text>{title}</Text>
        {rows.map((row,index)=><View key={index} style={[styles.card,{borderColor:colors.hairline}]}>
          <TextInput accessibilityLabel={`${title} ${index+1} identifier`} value={row.key} onChangeText={key=>change(index,{key})} autoCapitalize="none" style={[styles.input,input]} />
          <TextInput accessibilityLabel={`${title} ${index+1} text`} value={row.text} onChangeText={text=>change(index,{text})} autoCapitalize="none" style={[styles.input,input]} />
          <View style={styles.row}>{button(`Move ${title} ${index+1} up`,()=>move(index,-1),index===0)}{button(`Move ${title} ${index+1} down`,()=>move(index,1),index===rows.length-1)}{button(`Remove ${title} ${index+1}`,()=>update(rows.filter((_,i)=>i!==index)))}</View>
        </View>)}
        {button(`Add ${title === 'Checks' ? 'check' : 'criterion'}`,()=>{
          let number=1; const prefix=title==='Checks'?'check':'AC-';
          while(rows.some(row=>row.key===prefix+number)) number++;
          update([...rows,{key:prefix+number,text:''}]);
        })}
      </View>;
    })}
    <Text>Plan Markdown</Text>
    <TextInput accessibilityLabel="Plan Markdown" multiline value={value} onChangeText={onChange} autoCapitalize="none" style={[styles.editor,input]} />
  </View>;
}

const styles = StyleSheet.create({
  root:{gap:10}, row:{flexDirection:'row',flexWrap:'wrap',gap:4}, card:{gap:6,borderWidth:1,borderRadius:8,padding:8},
  input:{borderWidth:1,borderRadius:8,padding:10,minHeight:44},
  button:{borderWidth:1,borderRadius:8,padding:9,minHeight:44,justifyContent:'center'},
  editor:{borderWidth:1,borderRadius:8,padding:12,minHeight:400,textAlignVertical:'top',fontFamily:'monospace'},
});
